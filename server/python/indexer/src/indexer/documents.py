"""근거 문서 색인 (SPEC-document-store 5장). document-indexer 서비스의 진입점이다.

Kafka `documents.index`(consumer group document-indexer)로 색인 요청을 받는다. 메시지에는 버전 ID와 종류만 있고,
상태의 기준은 backend DB다. 받으면 그 버전이 아직 대기·실패이고 최신일 때만 처리한다(중복·늦은 메시지 무시).
Kafka 없이(DISPATCH_MODE=http) 돌면 5초마다 backend의 대기 목록을 본다.

파이프라인: 처리 정보 확인 → 원본 받기 → PARSE → CHUNK → VALIDATE(공통 표준 런북) → EMBED·STORE → 결과 보고
원본은 backend 내부 API로 받는다. indexer에는 저장소 계정이 없다.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import Counter
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import openai
import psycopg
from openai import OpenAI

from indexer.chunk import ChunkHeader, chunk_document, clause_numbers
from indexer.main import DATABASE_URL, EMBEDDING_DIM, EMBEDDING_MODEL
from indexer.parse import ParseError, parse_document
from indexer.storage import Chunk, delete_document, ensure_schema, index_document

log = logging.getLogger(__name__)

TOPIC = "documents.index"
GROUP = "document-indexer"
POLL_SECONDS = 5
COMMON = "_common"
STANDARD_RUNBOOK = "runbook-standard"
CHUNKER = "v2"
SCOPE_NAMES = {COMMON: "MSP 공통"}


class Backend:
    """backend 내부 API (/internal/documents/**). INDEXER_BACKEND_TOKEN으로만 통한다."""

    def __init__(self, base: str | None = None, token: str | None = None):
        self.base = (base or os.environ.get("BACKEND_URL", "http://backend:8080")).rstrip("/")
        self.token = token if token is not None else os.environ.get("INDEXER_BACKEND_TOKEN", "")
        if not self.token:
            raise ValueError("INDEXER_BACKEND_TOKEN is required")

    def _call(self, method: str, path: str, body: dict | None = None) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        with urlopen(Request(f"{self.base}{path}", data=data, method=method, headers=headers), timeout=30) as r:
            return r.read()

    def version(self, version_id: str) -> dict:
        return json.loads(self._call("GET", f"/internal/documents/versions/{quote(version_id)}"))

    def original(self, version_id: str) -> bytes:
        return self._call("GET", f"/internal/documents/versions/{quote(version_id)}/original")

    def report(self, version_id: str, result: dict) -> bool:
        reply = json.loads(self._call("POST", f"/internal/documents/versions/{quote(version_id)}/result", result))
        return bool(reply.get("applied"))

    def document(self, document_id: str) -> dict:
        return json.loads(self._call("GET", f"/internal/documents/{quote(document_id)}"))

    def chunks_removed(self, document_id: str) -> None:
        self._call("POST", f"/internal/documents/{quote(document_id)}/chunks-removed", {})

    def pending(self) -> list[dict]:
        return json.loads(self._call("GET", "/internal/documents/pending"))


class Failure(Exception):
    """색인 실패. stage: PARSE | CHUNK | VALIDATE | EMBED | STORE. 사유에 원문·비밀 값을 넣지 않는다."""

    def __init__(self, stage: str, reason: str, retryable: bool):
        super().__init__(reason)
        self.stage, self.reason, self.retryable = stage, reason, retryable


def should_index(info: dict) -> bool:
    """중복·늦은 메시지는 건너뛴다: 최신 버전이 아니거나, 삭제됐거나, 이미 끝났거나, 다시 시도하지 않을 실패다."""
    if info.get("deleted") or not info.get("latest"):
        return False
    return info.get("status") == "PENDING" or (info.get("status") == "FAILED" and bool(info.get("retryable")))


def check_standard_runbook(found: list[str], reference: list[str] | None) -> None:
    """공통 표준 런북의 조항 번호가 검색 버전과 같아야 한다 (SPEC 5.5). agent의 조치 검사가 번호에 묶여 있다."""
    counts = Counter(found)
    duplicated = sorted(c for c, n in counts.items() if n > 1)
    if not found:
        raise Failure("VALIDATE", "조항 제목을 찾지 못했다 (RB-STD-0N). PDF 제목 추정이 실패했으면 Word 제목 스타일이나 "
                                  "Markdown으로 올린다", False)
    if duplicated:
        raise Failure("VALIDATE", f"같은 조항 번호가 여러 번 나온다: {', '.join(duplicated)}", False)
    if reference is not None and set(found) != set(reference):
        missing = sorted(set(reference) - set(found))
        added = sorted(set(found) - set(reference))
        parts = ([f"빠짐 {', '.join(missing)}"] if missing else []) + ([f"늘어남 {', '.join(added)}"] if added else [])
        raise Failure("VALIDATE", f"공통 표준 런북의 조항 번호가 바뀌었다: {', '.join(parts)}. agent의 조치 검사가 "
                                  "조항 번호에 묶여 있어 색인하지 않았다", False)


def build_chunks(info: dict, data: bytes) -> tuple[list[Chunk], list[str] | None, str]:
    """(저장할 조각, 공통 표준 런북의 조항 번호, 추출 본문). 실패는 Failure로 던진다."""
    try:
        parsed = parse_document(data, info["format"])
    except ParseError as e:
        raise Failure("PARSE", str(e), e.retryable) from None
    scope = SCOPE_NAMES.get(info["tenantId"], info["tenantId"])
    pieces = chunk_document(parsed, ChunkHeader(info["title"], scope, info["docType"]))
    if not pieces:
        raise Failure("CHUNK", "본문이 비어 있어 색인할 내용이 없다", False)
    clauses = None
    if info["tenantId"] == COMMON and info["docId"] == STANDARD_RUNBOOK:
        clauses = clause_numbers(parsed)
        check_standard_runbook(clauses, info.get("referenceClauses"))
    version = hashlib.sha256(data).hexdigest()[:12]
    chunks = [Chunk(info["tenantId"], info["docId"], info["docType"], info["title"], version, p.chunk_no,
                    p.section or info["title"], p.content, p.section_path, info["format"], info["version"], CHUNKER)
              for p in pieces]
    return chunks, clauses, parsed.text


def index_version(backend: Backend, conn, embedder, version_id: str) -> str:
    info = backend.version(version_id)
    if not should_index(info):
        return "skipped"
    try:
        chunks, clauses, extracted = build_chunks(info, backend.original(version_id))
        try:
            index_document(conn, embedder, chunks, EMBEDDING_MODEL)
        except openai.OpenAIError as e:
            raise Failure("EMBED", f"임베딩 API 오류 ({type(e).__name__})", True) from None
        except psycopg.Error as e:
            raise Failure("STORE", f"조각 저장 실패 ({type(e).__name__})", True) from None
    except Failure as f:
        backend.report(version_id, {"status": "FAILED", "stage": f.stage, "error": f.reason, "retryable": f.retryable})
        log.warning("%s/%s v%s 색인 실패 [%s] %s", info["tenantId"], info["docId"], info["version"], f.stage, f.reason)
        return "failed"
    binary = info["format"] in ("pdf", "docx")
    applied = backend.report(version_id, {"status": "INDEXED", "chunks": len(chunks), "clauses": clauses,
                                          "extracted": extracted if binary else None})
    log.info("%s/%s v%s 색인 %s (조각 %d개)", info["tenantId"], info["docId"], info["version"],
             "완료" if applied else "결과 무시(더 새 버전이 있다)", len(chunks))
    return "indexed" if applied else "skipped"


def remove_document(backend: Backend, conn, document_id: str) -> str:
    info = backend.document(document_id)
    if not info.get("deleted"):
        return "skipped"
    removed = delete_document(conn, info["tenantId"], info["docId"])
    backend.chunks_removed(document_id)
    log.info("%s/%s 삭제: 조각 %d개", info["tenantId"], info["docId"], removed)
    return "deleted"


def handle(backend: Backend, conn, embedder, message: dict) -> str:
    if message.get("kind") == "DELETE":
        return remove_document(backend, conn, str(message["documentId"]))
    if message.get("kind") == "INDEX" and message.get("documentVersionId"):
        return index_version(backend, conn, embedder, str(message["documentVersionId"]))
    log.warning("알 수 없는 메시지를 건너뛴다: %s", message.get("kind"))
    return "skipped"


# ── 실행 ───────────────────────────────────────────────────────

class Worker:
    """DB 연결과 임베딩 클라이언트를 들고 메시지를 하나씩 처리한다. 연결이 끊기면 다시 연다."""

    def __init__(self, backend: Backend):
        self.backend = backend
        self.embedder = OpenAI(timeout=30, max_retries=1)
        self.conn = None

    def _connection(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(DATABASE_URL, autocommit=True)
            ensure_schema(self.conn, EMBEDDING_DIM)
        return self.conn

    def handle(self, message: dict) -> str:
        try:
            return handle(self.backend, self._connection(), self.embedder, message)
        except psycopg.OperationalError:
            self.conn = None
            raise


async def consume(worker: Worker) -> None:
    from aiokafka import AIOKafkaConsumer, TopicPartition

    consumer = AIOKafkaConsumer(TOPIC, bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
                                group_id=GROUP, enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    log.info("document-indexer: %s 구독 시작", TOPIC)
    try:
        async for record in consumer:
            try:
                message = json.loads(record.value)
                outcome = await asyncio.to_thread(worker.handle, message)
                log.debug("%s → %s", message, outcome)
            except HTTPError as e:
                if 400 <= e.code < 500:   # 없는 버전·잘못된 요청. 다시 받아도 같다
                    log.error("색인 요청을 건너뛴다 (backend %d)", e.code)
                    await consumer.commit()
                    continue
                log.error("색인 요청 처리 실패 (backend %d), 5초 뒤 다시 시도", e.code)
                await asyncio.sleep(5)
                consumer.seek(TopicPartition(record.topic, record.partition), record.offset)
                continue
            except (OSError, psycopg.Error) as e:
                # backend·DB에 닿지 않는다. 오프셋을 그대로 두고 같은 메시지부터 다시 받는다
                log.error("색인 요청 처리 실패 (%s), 5초 뒤 다시 시도", type(e).__name__)
                await asyncio.sleep(5)
                consumer.seek(TopicPartition(record.topic, record.partition), record.offset)
                continue
            except (ValueError, KeyError) as e:
                log.error("잘못된 색인 요청을 건너뛴다 (%s)", type(e).__name__)
            await consumer.commit()
    finally:
        await consumer.stop()


def poll(worker: Worker) -> None:
    log.info("document-indexer: Kafka 없이 %d초마다 대기 목록을 본다", POLL_SECONDS)
    while True:
        try:
            for message in worker.backend.pending():
                worker.handle(message)
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시 본다. 예외 본문에는 접속 정보가 있을 수 있다
            log.error("대기 목록 처리 실패 (%s)", type(e).__name__)
        time.sleep(POLL_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker(Backend())
    if os.environ.get("DISPATCH_MODE", "kafka") == "http":
        poll(worker)
    else:
        asyncio.run(consume(worker))


if __name__ == "__main__":
    main()

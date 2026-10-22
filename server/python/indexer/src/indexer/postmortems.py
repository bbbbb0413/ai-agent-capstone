"""종료된 장애 보고서를 backend API로 읽어 고객사별 회고 문서로 동기화한다.

app 스키마에는 접근하지 않는다. 매 순회마다 전체 페이지를 조회해 늦게 도착한 보고서와 수정도 반영한다.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

import psycopg
from openai import OpenAI

from indexer.main import DATABASE_URL, DOCUMENTS_DIR, EMBEDDING_DIM, EMBEDDING_MODEL
from indexer.storage import Chunk, ensure_schema, index_document

log = logging.getLogger(__name__)
DOC_PREFIX = "postmortem-incident-"


def tenant_ids() -> list[str]:
    configured = os.environ.get("POSTMORTEM_TENANTS", "")
    tenants = [t.strip() for t in configured.split(",") if t.strip()] if configured else [
        p.name for p in DOCUMENTS_DIR.iterdir() if p.is_dir() and p.name != "_common"
    ]
    if not tenants or any(not re.fullmatch(r"[a-zA-Z0-9_-]+", t) or t == "_common" for t in tenants):
        raise ValueError("POSTMORTEM_TENANTS에 고객사 ID를 지정하거나 고객사 문서 디렉터리를 만든다")
    return sorted(set(tenants))


def fetch_page(tenant: str, after_id: int) -> list[dict]:
    token = os.environ.get("SERVICE_TOKEN", "")
    if not token:
        raise ValueError("SERVICE_TOKEN is required")
    base = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/")
    request = Request(f"{base}/internal/tenants/{quote(tenant, safe='')}/postmortems?afterId={after_id}",
                      headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant})
    with urlopen(request, timeout=15) as response:
        rows = json.load(response)
    if not isinstance(rows, list):
        raise ValueError("invalid postmortem response")
    previous = after_id
    for row in rows:
        # 소유권·상태를 다시 확인한다. 잘못된 응답은 다른 고객사 이름으로 색인하지 않는다.
        if (row.get("tenantId") != tenant or row.get("status") != "RESOLVED"
                or not row.get("resolvedAt") or not (row.get("report") or "").strip()
                or type(row.get("id")) is not int or row["id"] <= previous):
            raise ValueError("invalid tenant, status or pagination in postmortem response")
        previous = row["id"]
    return rows


def report_chunks(tenant: str, incident: dict) -> list[Chunk]:
    if incident["tenantId"] != tenant or tenant == "_common":
        raise ValueError("postmortem tenant mismatch")
    doc_id = f"{DOC_PREFIX}{incident['id']}"
    title = f"장애 #{incident['id']}: {incident['title']}"
    metadata = (f"과거 장애 #{incident['id']} · {incident.get('service') or '서비스 미지정'}\n"
                f"제목: {incident['title']}\n"
                f"발생: {incident['startedAt']} / 종료: {incident['resolvedAt']}\n"
                "상태: RESOLVED. 자동 수집한 AI 보고서(미검토). 추정 원인과 조치 결과는 당시 기록이며, "
                "현재 장애의 원인·조치 성공을 보장하지 않는다. 현재 지표·계약·런북을 다시 확인한다.\n\n")
    text = incident["report"].strip()
    version = hashlib.sha256((metadata + text).encode()).hexdigest()[:12]
    chunks = []
    for part in re.split(r"(?m)^(?=## )", text):
        if not part.strip():
            continue
        section = part.splitlines()[0].removeprefix("## ") if part.startswith("## ") else "보고서"
        # 긴 조사 기록도 임베딩 입력 한도를 넘지 않게 나누고 각 조각에 출처를 붙인다.
        for offset in range(0, len(part), 4000):
            chunks.append(Chunk(tenant, doc_id, "postmortem", title, version, len(chunks), section,
                                metadata + part[offset:offset + 4000]))
    return chunks


def sync_tenant(conn, embedder, tenant: str) -> int:
    after_id, changed = 0, 0
    while rows := fetch_page(tenant, after_id):
        for incident in rows:
            changed += index_document(conn, embedder, report_chunks(tenant, incident), EMBEDDING_MODEL)
        after_id = rows[-1]["id"]
    return changed


def sync_once() -> bool:
    success = True
    with OpenAI(timeout=30, max_retries=1) as embedder, \
            psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        ensure_schema(conn, EMBEDDING_DIM)
        for tenant in tenant_ids():
            try:
                changed = sync_tenant(conn, embedder, tenant)
                log.info("%s: 회고 %d건 갱신", tenant, changed)
            except Exception as exc:
                # 예외 본문에는 접속 정보나 보고서가 들어갈 수 있어 유형만 남긴다.
                log.error("%s: 회고 동기화 실패 (%s), 다음 순회에서 재시도", tenant, type(exc).__name__)
                success = False
    return success


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="주기적으로 동기화한다")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("interval must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            success = sync_once()
        except Exception as exc:
            log.error("회고 동기화 실패 (%s)", type(exc).__name__)
            success = False
        if not args.watch:
            raise SystemExit(0 if success else 1)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

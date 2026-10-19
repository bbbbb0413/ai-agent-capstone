"""문서 색인 파이프라인 (Pipe and Filter): Documents → Chunking → Embedding → Indexing.

DOCUMENTS_DIR/{tenant_id}/*.md 를 읽는다. 파일 이름 앞부분으로 문서 종류를 정한다.
  sla-*.md → sla, runbook-*.md → runbook, postmortem-*.md → postmortem
조항(## 제목) 단위로 자르고, 질문 검색과 같은 Embedding 모델로 벡터화한다.
같은 문서를 다시 색인하면 이전 chunk를 지우고 새로 넣는다.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://opslens_indexer@localhost:5432/opslens")
DOCUMENTS_DIR = Path(os.environ.get("DOCUMENTS_DIR", "data/documents"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))

DOC_TYPES = {"sla": "sla", "runbook": "runbook", "postmortem": "postmortem"}


@dataclass
class Chunk:
    tenant_id: str
    doc_id: str
    doc_type: str
    title: str
    version: str
    chunk_index: int
    section: str
    content: str


def chunk_document(tenant_id: str, path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    doc_type = DOC_TYPES.get(path.stem.split("-", 1)[0])
    if doc_type is None:
        raise ValueError(f"{path.name}: 파일 이름은 sla-, runbook-, postmortem- 으로 시작해야 한다")
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    version = hashlib.sha256(text.encode()).hexdigest()[:12]

    # "## " 제목마다 조항 하나. 제목 앞의 머리말은 버린다
    sections = re.split(r"^(?=## )", text, flags=re.MULTILINE)[1:]
    return [
        Chunk(tenant_id, path.stem, doc_type, title, version, i,
              section.splitlines()[0].removeprefix("## ").strip(), section.strip())
        for i, section in enumerate(sections)
    ]


def main() -> None:
    chunks = [
        chunk
        for tenant_dir in sorted(p for p in DOCUMENTS_DIR.iterdir() if p.is_dir())
        for path in sorted(tenant_dir.glob("*.md"))
        for chunk in chunk_document(tenant_dir.name, path)
    ]
    if not chunks:
        print(f"색인할 문서가 없다: {DOCUMENTS_DIR}")
        return

    embeddings = OpenAI().embeddings.create(model=EMBEDDING_MODEL, input=[c.content for c in chunks]).data

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id              BIGSERIAL PRIMARY KEY,
                tenant_id       TEXT NOT NULL,
                doc_id          TEXT NOT NULL,
                doc_type        TEXT NOT NULL,
                title           TEXT NOT NULL,
                version         TEXT NOT NULL,
                chunk_index     INT  NOT NULL,
                section         TEXT NOT NULL,
                content         TEXT NOT NULL,
                embedding       vector({EMBEDDING_DIM}) NOT NULL,
                embedding_model TEXT NOT NULL,
                indexed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_tenant ON chunks (tenant_id, doc_type)")
        for doc in {(c.tenant_id, c.doc_id) for c in chunks}:
            conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND doc_id = %s", doc)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (tenant_id, doc_id, doc_type, title, version, chunk_index, section, content,
                                    embedding, embedding_model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [(c.tenant_id, c.doc_id, c.doc_type, c.title, c.version, c.chunk_index, c.section, c.content,
                  e.embedding, EMBEDDING_MODEL) for c, e in zip(chunks, embeddings, strict=True)],
            )
    print(f"색인 완료: 문서 {len({(c.tenant_id, c.doc_id) for c in chunks})}개, chunk {len(chunks)}개 ({EMBEDDING_MODEL})")


if __name__ == "__main__":
    main()

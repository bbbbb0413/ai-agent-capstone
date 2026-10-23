"""문서와 자동 회고가 공유하는 색인 저장소. 같은 내용·모델은 다시 임베딩하지 않는다."""

from dataclasses import dataclass


@dataclass
class Chunk:
    tenant_id: str
    doc_id: str
    doc_type: str
    title: str
    version: str          # 원본 해시 앞 12자리. 같은 내용·모델이면 다시 임베딩하지 않는다
    chunk_index: int
    section: str
    content: str
    # 근거 문서 저장소(SPEC-document-store 3.3). 회고는 기본값을 쓴다
    section_path: str = ""
    source_format: str = "postmortem"
    document_version: int | None = None
    chunker: str = "v1"   # 조각내는 규칙. 바뀌면 내용이 같아도 다시 만든다 (v2: 형식별 파서 + 공통 청커)


def ensure_schema(conn, dimensions: int) -> None:
    if not 1 <= dimensions <= 16000:
        raise ValueError("invalid embedding dimensions")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('opslens:chunks-schema', 0))")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, doc_id TEXT NOT NULL,
                doc_type TEXT NOT NULL, title TEXT NOT NULL, section TEXT NOT NULL,
                version TEXT NOT NULL, chunk_index INT NOT NULL, content TEXT NOT NULL,
                embedding vector({dimensions}) NOT NULL, embedding_model TEXT NOT NULL,
                indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_tenant ON chunks (tenant_id, doc_type)")
        conn.execute("""
            ALTER TABLE chunks
                ADD COLUMN IF NOT EXISTS section_path TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS source_format TEXT NOT NULL DEFAULT 'md',
                ADD COLUMN IF NOT EXISTS document_version INT,
                ADD COLUMN IF NOT EXISTS chunker TEXT NOT NULL DEFAULT 'v1'
        """)
        conn.execute("UPDATE chunks SET source_format = 'postmortem' WHERE doc_type = 'postmortem' AND source_format = 'md'")


def index_document(conn, embedder, chunks: list[Chunk], model: str) -> bool:
    if not chunks:
        return False
    first = chunks[0]
    with conn.transaction():
        # 수동 색인과 주기 작업이 겹쳐도 문서 하나를 중복 저장하지 않는다.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     (f"{first.tenant_id}/{first.doc_id}",))
        # 지난 버전이 늦게 끝나도 최신 버전의 조각을 덮지 않는다
        newest = conn.execute("SELECT max(document_version) FROM chunks WHERE tenant_id = %s AND doc_id = %s",
                              (first.tenant_id, first.doc_id)).fetchone()[0]
        if first.document_version is not None and newest is not None and newest > first.document_version:
            return False
        current = conn.execute(
            "SELECT version, embedding_model, chunker, count(*) FROM chunks "
            "WHERE tenant_id = %s AND doc_id = %s GROUP BY version, embedding_model, chunker",
            (first.tenant_id, first.doc_id),
        ).fetchall()
        if current == [(first.version, model, first.chunker, len(chunks))]:
            # 같은 내용을 되돌리기로 다시 올렸다. 임베딩은 그대로 두고 버전 번호만 맞춘다
            conn.execute("UPDATE chunks SET document_version = %s WHERE tenant_id = %s AND doc_id = %s",
                         (first.document_version, first.tenant_id, first.doc_id))
            return False
        response = embedder.embeddings.create(model=model, input=[c.content for c in chunks])
        embeddings = sorted(response.data, key=lambda item: item.index)
        if [e.index for e in embeddings] != list(range(len(chunks))):
            raise ValueError("embedding response does not match chunks")
        conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND doc_id = %s",
                     (first.tenant_id, first.doc_id))
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO chunks (tenant_id, doc_id, doc_type, title, section, version, chunk_index,
                                       content, embedding, embedding_model, section_path, source_format,
                                       document_version, chunker)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s)""",
                [(c.tenant_id, c.doc_id, c.doc_type, c.title, c.section, c.version, c.chunk_index,
                  c.content, e.embedding, model, c.section_path, c.source_format, c.document_version, c.chunker)
                 for c, e in zip(chunks, embeddings, strict=True)],
            )
    return True


def delete_document(conn, tenant_id: str, doc_id: str) -> int:
    """삭제한 문서의 조각을 지운다. 다시 불러도 결과가 같다."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{tenant_id}/{doc_id}",))
        return conn.execute("DELETE FROM chunks WHERE tenant_id = %s AND doc_id = %s", (tenant_id, doc_id)).rowcount

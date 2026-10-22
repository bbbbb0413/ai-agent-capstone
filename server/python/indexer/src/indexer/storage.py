"""문서와 자동 회고가 공유하는 색인 저장소. 같은 내용·모델은 다시 임베딩하지 않는다."""

from dataclasses import dataclass


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


def index_document(conn, embedder, chunks: list[Chunk], model: str) -> bool:
    if not chunks:
        return False
    first = chunks[0]
    with conn.transaction():
        # 수동 색인과 주기 작업이 겹쳐도 문서 하나를 중복 저장하지 않는다.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     (f"{first.tenant_id}/{first.doc_id}",))
        current = conn.execute(
            "SELECT version, embedding_model, count(*) FROM chunks "
            "WHERE tenant_id = %s AND doc_id = %s GROUP BY version, embedding_model",
            (first.tenant_id, first.doc_id),
        ).fetchall()
        if current == [(first.version, model, len(chunks))]:
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
                                       content, embedding, embedding_model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)""",
                [(c.tenant_id, c.doc_id, c.doc_type, c.title, c.section, c.version, c.chunk_index,
                  c.content, e.embedding, model) for c, e in zip(chunks, embeddings, strict=True)],
            )
    return True

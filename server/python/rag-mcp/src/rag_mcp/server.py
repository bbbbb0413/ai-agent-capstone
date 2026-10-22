"""rag-mcp: 고객사별 근거 문서를 검색하고 원문을 Resource로 제공한다.

- docs 스키마를 읽기 전용 계정으로만 조회한다. 업무 데이터는 바꾸지 않는다.
- 고객사 필터는 요청 헤더(X-Tenant-Id)로 서버가 강제한다. 모델이 인자로 다른 고객사를 고를 수 없다.
- MSP 공통 문서(tenant_id = '_common', 표준 런북)는 모든 고객사가 함께 검색·조회한다. 다른 고객사 문서는 계속 볼 수 없다.
- 질문 Embedding은 색인(indexer)과 같은 모델을 쓴다.
"""

import os
from typing import Any, Literal

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.dependencies import get_http_headers
from openai import AsyncOpenAI
from pgvector.psycopg import register_vector_async
from starlette.middleware import Middleware

from rag_mcp.auth import ServiceTokenMiddleware

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://opslens_rag@localhost:5432/opslens")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")

READ = {"readOnlyHint": True, "destructiveHint": False}
COMMON = "_common"  # data/documents/_common/ (indexer가 디렉터리 이름을 tenant_id로 쓴다)

mcp = FastMCP("rag")
_openai: AsyncOpenAI | None = None


def _embedder() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI()  # OPENAI_API_KEY 환경변수를 읽는다
    return _openai


def _tenant() -> str:
    tenant = get_http_headers().get("x-tenant-id")
    if not tenant:
        raise ToolError("고객사 컨텍스트(X-Tenant-Id)가 없다. agent의 MCP 연결 설정을 확인한다")
    return tenant


async def _connect() -> psycopg.AsyncConnection:
    # TODO: 요청이 늘면 psycopg_pool로 연결 풀을 쓴다
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    await register_vector_async(conn)
    return conn


@mcp.tool(annotations=READ)
async def search_documents(
    query: str,
    doc_type: Literal["sla", "runbook", "postmortem"] | None = None,
    top_k: int = 5,
    scope: Literal["all", "common", "tenant"] = "all",
    exclude_doc_id: str | None = None,
) -> list[dict[str, Any]]:
    """현재 고객사의 근거 문서와 MSP 공통 문서(표준 런북)에서 질문과 가까운 조항을 찾는다.
    doc_type: sla(SLA 계약서), runbook(런북: 표준 런북과 서비스 런북), postmortem(과거 장애 회고). 비우면 전체에서 찾는다.
    결과마다 문서 id, 제목, 조항(section), 버전, 본문, 유사도(score, 1에 가까울수록 가깝다), 원문 주소(uri),
    범위(scope: common=MSP 표준, tenant=이 고객사)를 돌려준다. 답변에 근거를 붙일 때 section과 uri를 함께 인용한다.
    scope 인자로 검색 범위를 좁힐 수 있다: all(둘 다), common(표준 런북만), tenant(이 고객사 문서만).
    exclude_doc_id는 현재 장애의 회고를 과거 사례로 다시 읽지 않도록 제외할 문서 ID다."""
    tenant = _tenant()
    owners = {"all": [tenant, COMMON], "common": [COMMON], "tenant": [tenant]}[scope]
    top_k = max(1, min(top_k, 10))
    embedding = (await _embedder().embeddings.create(model=EMBEDDING_MODEL, input=query)).data[0].embedding

    async with await _connect() as conn:
        rows = await (await conn.execute(
            """
            SELECT tenant_id, doc_id, doc_type, title, section, version, content,
                   1 - (embedding <=> %(e)s::vector) AS score
            FROM chunks
            WHERE tenant_id = ANY(%(o)s) AND (%(d)s::text IS NULL OR doc_type = %(d)s)
              AND (%(exclude)s::text IS NULL OR doc_id <> %(exclude)s)
            ORDER BY embedding <=> %(e)s::vector
            LIMIT %(k)s
            """,
            {"e": embedding, "o": owners, "d": doc_type, "k": top_k, "exclude": exclude_doc_id},
        )).fetchall()

    return [
        {"doc_id": doc_id, "doc_type": kind, "title": title, "section": section, "version": version,
         "content": content, "score": round(float(score), 3), "uri": f"docs://{owner}/{doc_id}",
         "scope": "common" if owner == COMMON else "tenant"}
        for owner, doc_id, kind, title, section, version, content, score in rows
    ]


@mcp.resource("docs://{tenant}/{doc_id}", mime_type="text/markdown")
async def read_document(tenant: str, doc_id: str) -> str:
    """근거 패널에 보여줄 문서 원문. 공통 문서가 아닌데 요청한 고객사와 문서의 고객사가 다르면 거부한다."""
    if tenant not in (_tenant(), COMMON):
        raise ResourceError("다른 고객사의 문서는 읽을 수 없다")
    async with await _connect() as conn:
        rows = await (await conn.execute(
            "SELECT section, content FROM chunks WHERE tenant_id = %s AND doc_id = %s ORDER BY chunk_index",
            (tenant, doc_id),
        )).fetchall()
    if not rows:
        raise ResourceError(f"문서를 찾을 수 없다: {doc_id}")
    return "\n\n".join(content for _, content in rows)


@mcp.prompt
def postmortem_draft(incident_summary: str) -> str:
    """사용자가 고르는 포스트모템 초안 템플릿 (선택 기능)."""
    return (
        "다음 인시던트의 포스트모템 초안을 작성한다. 항목: 요약, 영향(시간·범위), 타임라인, 근본 원인, "
        "조치, 재발 방지 과제. 근거가 없는 수치는 쓰지 않는다.\n\n"
        f"인시던트 요약:\n{incident_summary}"
    )


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "9102")),
        middleware=[Middleware(ServiceTokenMiddleware, token=SERVICE_TOKEN)],
        show_banner=False,
    )

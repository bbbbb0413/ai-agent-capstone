"""실제 pgvector 저장/검색을 연결한다. 외부 임베딩 API만 대역을 사용한다."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from fastmcp.exceptions import ResourceError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "indexer" / "src"))
from indexer.postmortems import report_chunks
from indexer.storage import ensure_schema, index_document
from rag_mcp import server


@unittest.skipUnless(os.environ.get("OPSLENS_TEST_DSN"), "별도 pgvector DB가 필요하다")
class PostmortemSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = psycopg.connect(os.environ["OPSLENS_TEST_DSN"], autocommit=True)
        self.schema = "rag_test_" + uuid4().hex
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.conn.execute(f"SET search_path TO {self.schema}, public")
        ensure_schema(self.conn, 2)
        embedder = Mock()
        embedder.embeddings.create.side_effect = lambda **kwargs: SimpleNamespace(data=[
            SimpleNamespace(index=i, embedding=[1.0, 0.0]) for i in range(len(kwargs["input"]))])
        for tenant, incident_id in (("tenant-a", 1), ("tenant-a", 2), ("tenant-b", 1)):
            incident = {"id": incident_id, "tenantId": tenant, "service": "order-api", "title": "풀 고갈",
                        "startedAt": "2026-10-20", "resolvedAt": "2026-10-21",
                        "report": "## 추정 원인\n풀 설정 변경\n## 조치와 결과\n롤백 후 회복"}
            index_document(self.conn, embedder, report_chunks(tenant, incident), "test-model")
        self.patches = [
            patch.object(server, "DATABASE_URL", make_conninfo(os.environ["OPSLENS_TEST_DSN"],
                         options=f"-csearch_path={self.schema},public")),
            patch.object(server, "get_http_headers", return_value={"x-tenant-id": "tenant-a"}),
            patch.object(server, "_embedder", return_value=SimpleNamespace(embeddings=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])]))))),
        ]
        for patcher in self.patches:
            patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.close()

    async def test_indexed_report_is_searchable_with_self_and_tenant_exclusion(self):
        results = await server.search_documents("풀 고갈", doc_type="postmortem", scope="tenant", top_k=10,
                                               exclude_doc_id="postmortem-incident-2")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["uri"] == "docs://tenant-a/postmortem-incident-1" for r in results))
        self.assertTrue(all("미검토" in r["content"] for r in results))
        document = await server.read_document("tenant-a", "postmortem-incident-1")
        self.assertIn("롤백 후 회복", document)
        with self.assertRaises(ResourceError):
            await server.read_document("tenant-b", "postmortem-incident-1")

    async def test_history_does_not_appear_in_runbook_search(self):
        self.assertEqual(await server.search_documents("풀 고갈", doc_type="runbook", scope="tenant"), [])


if __name__ == "__main__":
    unittest.main()

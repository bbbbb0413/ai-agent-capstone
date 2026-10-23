"""원문 참조: 런북·계약서는 backend(근거 문서 저장소)에서, 회고는 조각에서 (SPEC-document-store 7장)."""

import io
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from fastmcp.exceptions import ResourceError

from rag_mcp import server


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ReadDocumentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = [patch.object(server, "_tenant", AsyncMock(return_value="tenant-a")),
                        patch.object(server, "get_http_headers", return_value={"x-task-token": "task-jwt"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    async def test_stored_document_comes_from_the_backend_with_both_tokens(self):
        seen = []

        def urlopen(request, timeout):
            seen.append(request)
            return FakeResponse("# B사 계약서\n\n머리말까지 원문 그대로".encode())

        with patch.object(server, "urlopen", side_effect=urlopen):
            text = await server.read_document("tenant-a", "sla-contract")
            await server.read_document("_common", "runbook-standard")   # 공통 문서는 어느 고객사 작업이든 읽는다
        self.assertIn("머리말까지", text)
        self.assertTrue(seen[0].full_url.endswith("/internal/tenants/tenant-a/documents/sla-contract"))
        self.assertEqual(seen[0].get_header("X-task-token"), "task-jwt")
        self.assertTrue(seen[0].get_header("Authorization").startswith("Bearer "))
        self.assertTrue(seen[1].full_url.endswith("/internal/tenants/_common/documents/runbook-standard"))

    async def test_other_tenant_is_refused_before_calling_the_backend(self):
        with patch.object(server, "urlopen") as urlopen, self.assertRaises(ResourceError):
            await server.read_document("tenant-b", "sla-contract")
        urlopen.assert_not_called()

    async def test_backend_errors_become_resource_errors(self):
        for code, reason in ((404, "찾을 수 없다"), (403, "다른 고객사"), (500, "backend 500")):
            error = HTTPError("u", code, "x", {}, None)
            with patch.object(server, "urlopen", side_effect=error), self.assertRaises(ResourceError) as e:
                await server.read_document("tenant-a", "sla-contract")
            self.assertIn(reason, str(e.exception))

    async def test_postmortems_are_read_from_chunks_not_the_store(self):
        conn = AsyncMock()
        conn.__aenter__.return_value = conn
        conn.execute.return_value.fetchall = AsyncMock(return_value=[("원인", "회고 본문")])
        with patch.object(server, "_connect", AsyncMock(return_value=conn)), patch.object(server, "urlopen") as urlopen:
            self.assertEqual(await server.read_document("tenant-a", "postmortem-incident-1"), "회고 본문")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

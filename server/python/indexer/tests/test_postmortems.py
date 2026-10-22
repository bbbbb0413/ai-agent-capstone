import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from indexer import postmortems


def incident(incident_id=1, **overrides):
    return {"id": incident_id, "tenantId": "tenant-a", "service": "order-api", "title": "커넥션 풀 고갈",
            "status": "RESOLVED", "startedAt": "2026-10-20T00:00:00Z", "resolvedAt": "2026-10-20T00:10:00Z",
            "report": "## 추정 원인\n잘못된 풀 설정\n\n## 조치와 결과\n롤백 후 회복", **overrides}


class PostmortemTests(unittest.TestCase):
    def test_each_chunk_preserves_provenance_and_unreviewed_status(self):
        chunks = postmortems.report_chunks("tenant-a", incident())
        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertEqual((chunk.tenant_id, chunk.doc_id, chunk.doc_type),
                             ("tenant-a", "postmortem-incident-1", "postmortem"))
            self.assertIn("미검토", chunk.content)
            self.assertIn("order-api", chunk.content)
            self.assertIn("2026-10-20T00:10:00Z", chunk.content)

    def test_content_and_resolution_changes_invalidate_version(self):
        old = postmortems.report_chunks("tenant-a", incident())[0].version
        self.assertEqual(old, postmortems.report_chunks("tenant-a", incident())[0].version)
        for change in ({"report": "## 수정\n새 원인"}, {"resolvedAt": "2026-10-21T00:10:00Z"}):
            self.assertNotEqual(old, postmortems.report_chunks("tenant-a", incident(**change))[0].version)

    def test_long_report_is_bounded_and_wrong_tenant_is_rejected(self):
        chunks = postmortems.report_chunks("tenant-a", incident(report="## 원인\n" + "가" * 12000))
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c.content) < 5000 for c in chunks))
        with self.assertRaises(ValueError):
            postmortems.report_chunks("tenant-b", incident())

    @patch.dict(os.environ, {"SERVICE_TOKEN": "test", "BACKEND_URL": "http://backend:8080"})
    def test_http_request_is_tenant_scoped_and_validates_response(self):
        def response(rows):
            return io.BytesIO(json.dumps(rows).encode())
        with patch.object(postmortems, "urlopen", return_value=response([incident()])) as send:
            self.assertEqual(postmortems.fetch_page("tenant-a", 0), [incident()])
            request = send.call_args.args[0]
            self.assertEqual(request.get_header("X-tenant-id"), "tenant-a")
            self.assertIn("/tenant-a/postmortems?afterId=0", request.full_url)
        for row in (incident(tenantId="tenant-b"), incident(status="OPEN"), incident(report=""),
                    incident(resolvedAt=None), incident(0)):
            with patch.object(postmortems, "urlopen", return_value=response([row])):
                with self.assertRaises(ValueError):
                    postmortems.fetch_page("tenant-a", 0)

    def test_all_pages_and_late_reports_are_scanned_again(self):
        with patch.object(postmortems, "fetch_page", side_effect=[[incident(2)], [incident(105)], [], []]) as fetch, \
                patch.object(postmortems, "index_document", return_value=True):
            self.assertEqual(postmortems.sync_tenant(Mock(), Mock(), "tenant-a"), 2)
            self.assertEqual(postmortems.sync_tenant(Mock(), Mock(), "tenant-a"), 0)
        self.assertEqual([call.args[1] for call in fetch.call_args_list], [0, 2, 105, 0])

    @patch.dict(os.environ, {"POSTMORTEM_TENANTS": "tenant-a,_common"})
    def test_common_scope_cannot_receive_incident_reports(self):
        with self.assertRaises(ValueError):
            postmortems.tenant_ids()


if __name__ == "__main__":
    unittest.main()

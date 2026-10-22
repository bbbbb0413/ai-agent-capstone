"""OPSLENS_TEST_DSN이 가리키는 별도 PostgreSQL에 임시 스키마를 만들어 검사한다."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4

import psycopg

from indexer.storage import ensure_schema, index_document
from indexer.postmortems import report_chunks
from test_postmortems import incident


@unittest.skipUnless(os.environ.get("OPSLENS_TEST_DSN"), "별도 PostgreSQL이 필요하다")
class StorageDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ["OPSLENS_TEST_DSN"], autocommit=True)
        self.schema = "test_" + uuid4().hex
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.conn.execute(f"SET search_path TO {self.schema}, public")
        ensure_schema(self.conn, 2)
        self.embedder = Mock()
        self.embedder.embeddings.create.side_effect = lambda **kwargs: SimpleNamespace(data=[
            SimpleNamespace(index=i, embedding=[1.0, 0.0]) for i in range(len(kwargs["input"]))])

    def tearDown(self):
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.close()

    def test_retries_skip_embeddings_and_revisions_replace_atomically(self):
        chunks = report_chunks("tenant-a", incident())
        self.assertTrue(index_document(self.conn, self.embedder, chunks, "test-model"))
        self.assertFalse(index_document(self.conn, self.embedder, chunks, "test-model"))
        self.assertEqual(self.embedder.embeddings.create.call_count, 1)
        revised = report_chunks("tenant-a", incident(report="## 원인\n수정된 원인"))
        self.assertTrue(index_document(self.conn, self.embedder, revised, "test-model"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0], 1)
        self.assertIn("수정된 원인", self.conn.execute("SELECT content FROM chunks").fetchone()[0])
        self.embedder.embeddings.create.side_effect = RuntimeError("embedding unavailable")
        with self.assertRaises(RuntimeError):
            index_document(self.conn, self.embedder, chunks, "test-model")
        self.assertIn("수정된 원인", self.conn.execute("SELECT content FROM chunks").fetchone()[0])

    def test_same_document_id_in_other_tenant_is_independent(self):
        for tenant in ("tenant-a", "tenant-b"):
            index_document(self.conn, self.embedder,
                           report_chunks(tenant, incident(tenantId=tenant)), "test-model")
        self.assertEqual(self.conn.execute("SELECT count(DISTINCT tenant_id) FROM chunks").fetchone()[0], 2)
        self.assertEqual(self.embedder.embeddings.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()

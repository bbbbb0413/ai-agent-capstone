"""문서 색인 흐름: 중복·늦은 메시지 무시, 단계별 실패 보고, 공통 표준 런북 검사 (SPEC-document-store 5장)."""

import unittest
from unittest.mock import patch

import openai

from indexer import documents
from samples import docx_file

RUNBOOK = "# MSP 표준 런북\n\n" + "".join(f"## RB-STD-0{i} 조항 {i}\n본문 {i}\n\n" for i in range(1, 8))
CLAUSES = [f"RB-STD-0{i}" for i in range(1, 8)]


class FakeBackend:
    def __init__(self, info: dict, original: bytes, applied: bool = True):
        self.info, self.data, self.applied = info, original, applied
        self.reports: list[dict] = []
        self.removed: list[str] = []

    def version(self, version_id):
        return self.info

    def original(self, version_id):
        return self.data

    def report(self, version_id, result):
        self.reports.append(result)
        return self.applied

    def document(self, document_id):
        return self.info

    def chunks_removed(self, document_id):
        self.removed.append(document_id)


def info(**overrides) -> dict:
    base = {"documentVersionId": "v-id", "documentId": "d-id", "tenantId": "tenant-b", "docId": "sla-contract",
            "docType": "sla", "title": "B사 SLA 계약서", "version": 3, "format": "md", "status": "PENDING",
            "retryable": None, "latest": True, "deleted": False, "referenceClauses": None}
    return {**base, **overrides}


class IndexVersionTests(unittest.TestCase):
    def run_index(self, backend, store=None):
        with patch.object(documents, "index_document", side_effect=store or (lambda *a: True)) as index:
            outcome = documents.index_version(backend, None, None, "v-id")
        return outcome, index

    def test_duplicate_late_or_finished_messages_are_skipped(self):
        for overrides in ({"latest": False}, {"deleted": True}, {"status": "INDEXED"}, {"status": "SUPERSEDED"},
                          {"status": "FAILED", "retryable": False}):
            backend = FakeBackend(info(**overrides), "# x\n\n## 1\n본문\n".encode())
            outcome, index = self.run_index(backend)
            self.assertEqual(outcome, "skipped", overrides)
            index.assert_not_called()
            self.assertEqual(backend.reports, [])
        backend = FakeBackend(info(status="FAILED", retryable=True), "# x\n\n## 1\n본문\n".encode())
        self.assertEqual(self.run_index(backend)[0], "indexed")   # 다시 시도할 실패는 처리한다

    def test_docx_chunks_carry_format_version_and_path_and_report_the_extracted_text(self):
        data = docx_file([("h1", "B사 SLA 계약서"), ("h2", "제5조 서비스 크레딧"), ("p", "크레딧을 준다.")])
        backend = FakeBackend(info(format="docx"), data)
        outcome, index = self.run_index(backend)
        self.assertEqual(outcome, "indexed")
        chunks = index.call_args.args[2]
        self.assertEqual([(c.source_format, c.document_version, c.chunker) for c in chunks], [("docx", 3, "v2")])
        self.assertEqual(chunks[0].section_path, "B사 SLA 계약서 > 제5조 서비스 크레딧")
        self.assertEqual(chunks[0].section, "제5조 서비스 크레딧")
        report = backend.reports[0]
        self.assertEqual((report["status"], report["chunks"]), ("INDEXED", 1))
        self.assertIn("크레딧을 준다.", report["extracted"])

    def test_markdown_reports_no_extracted_text(self):
        backend = FakeBackend(info(), "# 계약서\n\n## 제1조\n본문\n".encode())
        self.run_index(backend)
        self.assertIsNone(backend.reports[0]["extracted"])

    def test_failures_report_their_stage_and_whether_to_retry(self):
        cases = [
            (FakeBackend(info(format="pdf"), b"%PDF-1.4 garbage"), None, ("PARSE", False)),
            (FakeBackend(info(), b"   \n"), None, ("CHUNK", False)),
            (FakeBackend(info(), b"# x\n\n## 1\n\xeb\xb3\xb8\n"),
             openai.APIConnectionError(request=None), ("EMBED", True)),
        ]
        for backend, error, expected in cases:
            def store(*args, error=error):
                if error:
                    raise error
                return True
            self.assertEqual(self.run_index(backend, store)[0], "failed")
            report = backend.reports[0]
            self.assertEqual((report["status"], report["stage"], report["retryable"]), ("FAILED", *expected))

    def test_late_result_is_reported_but_not_applied(self):
        backend = FakeBackend(info(), b"# x\n\n## 1\n\xeb\xb3\xb8\n", applied=False)
        self.assertEqual(self.run_index(backend)[0], "skipped")


class StandardRunbookTests(unittest.TestCase):
    def common(self, **overrides):
        return info(tenantId="_common", docId="runbook-standard", docType="runbook", title="MSP 표준 런북", **overrides)

    def test_first_version_sets_the_reference_and_same_clauses_pass(self):
        backend = FakeBackend(self.common(referenceClauses=None), RUNBOOK.encode())
        with patch.object(documents, "index_document", return_value=True):
            documents.index_version(backend, None, None, "v-id")
        self.assertEqual(backend.reports[0]["clauses"], CLAUSES)

    def test_other_formats_are_checked_after_parsing(self):
        blocks = [("h1", "MSP 표준 런북")] + [b for i in range(1, 8) for b in (("h2", f"RB-STD-0{i} 조항"), ("p", "본문"))]
        backend = FakeBackend(self.common(format="docx", referenceClauses=CLAUSES), docx_file(blocks))
        with patch.object(documents, "index_document", return_value=True):
            self.assertEqual(documents.index_version(backend, None, None, "v-id"), "indexed")

    def test_changed_clause_numbers_are_rejected_and_not_stored(self):
        changed = RUNBOOK.replace("RB-STD-04", "RB-STD-08")
        backend = FakeBackend(self.common(referenceClauses=CLAUSES), changed.encode())
        with patch.object(documents, "index_document") as index:
            self.assertEqual(documents.index_version(backend, None, None, "v-id"), "failed")
        index.assert_not_called()
        report = backend.reports[0]
        self.assertEqual((report["stage"], report["retryable"]), ("VALIDATE", False))
        self.assertIn("빠짐 RB-STD-04", report["error"])
        self.assertIn("늘어남 RB-STD-08", report["error"])

    def test_missing_or_duplicated_clause_titles_are_rejected(self):
        for text, reason in (("# 런북\n\n## 개요\n본문\n", "조항 제목을 찾지 못했다"),
                             (RUNBOOK + "## RB-STD-01 또\n본문\n", "여러 번")):
            backend = FakeBackend(self.common(referenceClauses=CLAUSES), text.encode())
            with patch.object(documents, "index_document"):
                documents.index_version(backend, None, None, "v-id")
            self.assertIn(reason, backend.reports[0]["error"])

    def test_clause_check_applies_only_to_the_standard_runbook(self):
        backend = FakeBackend(info(tenantId="_common", docId="runbook-extra", docType="runbook",
                                   referenceClauses=CLAUSES), "# 부록\n\n## 참고\n본문\n".encode())
        with patch.object(documents, "index_document", return_value=True):
            self.assertEqual(documents.index_version(backend, None, None, "v-id"), "indexed")


class DeleteTests(unittest.TestCase):
    def test_deleted_documents_lose_their_chunks(self):
        backend = FakeBackend({"documentId": "d-id", "tenantId": "tenant-a", "docId": "runbook-x", "deleted": True}, b"")
        with patch.object(documents, "delete_document", return_value=3) as delete:
            self.assertEqual(documents.handle(backend, None, None, {"kind": "DELETE", "documentId": "d-id"}), "deleted")
        delete.assert_called_once_with(None, "tenant-a", "runbook-x")
        self.assertEqual(backend.removed, ["d-id"])

        backend.info["deleted"] = False   # 삭제하지 않은 문서에 온 잘못된 메시지면 지우지 않는다
        with patch.object(documents, "delete_document") as delete:
            self.assertEqual(documents.handle(backend, None, None, {"kind": "DELETE", "documentId": "d-id"}), "skipped")
        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()

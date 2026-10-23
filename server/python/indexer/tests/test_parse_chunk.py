"""형식별 파서와 공통 청커 (SPEC-document-store 5.3·5.4·5.6)."""

import unittest
from pathlib import Path

from indexer.chunk import MAX_TOKENS, ChunkHeader, chunk_document, clause_numbers, estimate_tokens
from indexer.parse import ParseError, PdfLine, infer_pdf_headings, parse_document, parse_markdown

from samples import blank_pdf, docx_file, pdf

SEED = Path(__file__).resolve().parents[3] / "data" / "documents"
HEADER = ChunkHeader("문서", "A사", "runbook")


def paths(text_or_parsed, fmt="md"):
    parsed = parse_document(text_or_parsed.encode(), fmt) if isinstance(text_or_parsed, str) else text_or_parsed
    return [c.section_path for c in chunk_document(parsed, HEADER)]


class MarkdownTests(unittest.TestCase):
    def test_headings_build_section_paths_and_the_title_line_alone_is_not_a_chunk(self):
        text = "# 런북\n\n## 1. 확인\n본문\n\n### 1.1 세부\n세부 본문\n\n## 2. 조치\n조치 본문\n"
        self.assertEqual(paths(text), ["런북 > 1. 확인", "런북 > 1. 확인 > 1.1 세부", "런북 > 2. 조치"])

    def test_intro_before_the_first_section_is_kept(self):
        self.assertEqual(paths("# 런북\n\n머리말이다.\n\n## 1. 확인\n본문\n"), ["런북", "런북 > 1. 확인"])
        self.assertEqual(paths("제목 없는 머리말\n\n## 1. 확인\n본문\n"), ["", "1. 확인"])

    def test_hash_lines_inside_a_code_fence_are_not_headings(self):
        parsed = parse_markdown("# 런북\n\n## 명령\n```bash\n# 이건 주석이다\nkubectl get pods\n```\n")
        self.assertEqual([h.title for h in parsed.headings], ["런북", "명령"])

    def test_text_files_have_no_headings(self):
        self.assertEqual(paths("# 제목처럼 보이지만\n본문\n", "txt"), [""])

    def test_chunk_carries_the_header_and_the_last_title_as_section(self):
        chunk = chunk_document(parse_markdown("# 런북\n\n## RB-STD-02 작업 스레드 고갈\n본문\n"),
                               ChunkHeader("MSP 표준 런북", "MSP 공통", "runbook"))[0]
        self.assertEqual(chunk.section, "RB-STD-02 작업 스레드 고갈")
        self.assertTrue(chunk.content.startswith(
            "[문서] MSP 표준 런북 | MSP 공통 | 런북 | 절 런북 > RB-STD-02 작업 스레드 고갈\n\n## RB-STD-02"))

    def test_long_sections_split_with_overlap_but_tables_stay_whole(self):
        rows = "\n".join(f"| 항목 {i} | " + "값 " * 20 + "|" for i in range(10))
        body = "# 표\n\n## 큰 절\n" + ("문장이다. " * 30 + "\n") * 20 + rows + "\n"
        chunks = chunk_document(parse_markdown(body), HEADER)
        self.assertGreater(len(chunks), 1)   # 700토큰을 넘는 절은 나뉜다
        prose = [c for c in chunks if "| 항목" not in c.content]
        self.assertTrue(prose and all(estimate_tokens(c.content) <= MAX_TOKENS for c in prose))
        tables = [c for c in chunks if "| 항목 0 |" in c.content]
        self.assertEqual(len(tables), 1)   # 표는 행 사이에서 끊지 않아 한 조각에 다 들어간다
        self.assertTrue(all(f"| 항목 {i} |" in tables[0].content for i in range(10)))


@unittest.skipUnless(SEED.exists(), "저장소 밖에서 실행: 시드 문서가 없다")
class SeedDocumentTests(unittest.TestCase):
    def test_each_seed_clause_is_one_chunk_as_before(self):
        """지금 Markdown 문서는 `## ` 조항 하나가 조각 하나다. 달라지는 것은 머리말 조각뿐이다."""
        for path in sorted(SEED.glob("*/*.md")):
            text = path.read_text(encoding="utf-8")
            clauses = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
            chunks = chunk_document(parse_markdown(text), HEADER)
            self.assertEqual([c.section for c in chunks if " > " in c.section_path], clauses, path.name)

    def test_standard_runbook_clause_numbers(self):
        parsed = parse_markdown((SEED / "_common" / "runbook-standard.md").read_text(encoding="utf-8"))
        self.assertEqual(clause_numbers(parsed), [f"RB-STD-0{i}" for i in range(1, 8)])


class DocxTests(unittest.TestCase):
    def test_heading_styles_and_tables_in_document_order(self):
        data = docx_file([("h1", "B사 SLA 계약서"), ("p", "머리말"), ("h2", "제5조 서비스 크레딧"),
                          ("p", "가용성이 목표 미만이면 크레딧을 준다."),
                          ("table", [["가용성", "크레딧"], ["99.0% 이상", "10%"], ["99.0% 미만", "25%"]]),
                          ("h2", "제6조 면책"), ("p", "천재지변은 제외한다.")])
        parsed = parse_document(data, "docx")
        self.assertEqual([(h.level, h.title) for h in parsed.headings],
                         [(1, "B사 SLA 계약서"), (2, "제5조 서비스 크레딧"), (2, "제6조 면책")])
        chunks = chunk_document(parsed, HEADER)
        self.assertEqual([c.section_path for c in chunks],
                         ["B사 SLA 계약서", "B사 SLA 계약서 > 제5조 서비스 크레딧", "B사 SLA 계약서 > 제6조 면책"])
        self.assertIn("| 99.0% 미만 | 25% |", chunks[1].content)   # 표는 그 조항 아래에 행 단위로 들어간다

    def test_broken_docx_is_a_non_retryable_parse_error(self):
        with self.assertRaises(ParseError) as e:
            parse_document(b"PK\x03\x04 not really a zip", "docx")
        self.assertFalse(e.exception.retryable)


class PdfTests(unittest.TestCase):
    def test_larger_font_lines_become_headings(self):
        body = [(10, f"Body sentence number {i} explains the service level in plain words.") for i in range(6)]
        data = pdf([(18, "Service Agreement")] + body[:3] + [(14, "Article 1 Availability")] + body[3:])
        parsed = parse_document(data, "pdf")
        self.assertEqual([(h.level, h.title) for h in parsed.headings],
                         [(1, "Service Agreement"), (2, "Article 1 Availability")])

    def test_numbered_clause_lines_are_headings_even_in_body_font(self):
        lines = [(10, "MSP standard runbook intro text that is long enough to be the body.")] * 3 + [
            (10, "RB-STD-01 Connection pool exhausted"), (10, "Roll back the last change."),
            (10, "RB-STD-02 Worker pool saturated"), (10, "Restart pods one by one.")]
        parsed = parse_document(pdf(lines), "pdf")
        self.assertEqual(clause_numbers(parsed), ["RB-STD-01", "RB-STD-02"])
        self.assertEqual(paths(parsed)[-1], "RB-STD-02 Worker pool saturated")

    def test_korean_article_pattern(self):
        lines = [PdfLine("제5조 서비스 크레딧", 0, 10), PdfLine("본문", 12, 10), PdfLine("제 6 조 면책", 20, 10),
                 PdfLine("제5조에 따라 지급한다", 30, 10)]
        found = infer_pdf_headings(lines, {10: 40})
        self.assertEqual([h.title for h in found], ["제5조 서비스 크레딧", "제 6 조 면책"])   # 문장 속 "제5조에"는 아니다

    def test_uniform_font_without_numbers_has_no_headings(self):
        parsed = parse_document(pdf([(10, f"Plain line {i}") for i in range(5)]), "pdf")
        self.assertEqual(parsed.headings, ())

    def test_scanned_and_broken_pdfs_fail_with_a_reason(self):
        for data, reason in ((blank_pdf(), "텍스트가 없는 PDF"), (b"%PDF-1.4 garbage", "PDF를 읽을 수 없다")):
            with self.assertRaises(ParseError) as e:
                parse_document(data, "pdf")
            self.assertIn(reason, str(e.exception))
            self.assertFalse(e.exception.retryable)


if __name__ == "__main__":
    unittest.main()

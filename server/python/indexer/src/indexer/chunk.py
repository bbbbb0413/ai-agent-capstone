"""공통 청커: 파서가 준 제목으로 절을 나누고, 긴 절만 줄 단위로 다시 나눈다 (SPEC-document-store 5.4).

ai-web-service index-worker의 chunk.py를 옮겼다(contracts/indexing/README.md §4·§6). 회의록 태그와
작성일·프로젝트 헤더는 뺐다. OpsLens 런북·계약 조항은 700토큰보다 짧아 조항 하나가 조각 하나다.
"""

import math
import re
from dataclasses import dataclass

from indexer.parse import ParsedDocument

TARGET_TOKENS = 400
MAX_TOKENS = 700
OVERLAP_TOKENS = 60

# 표와 코드 블록은 중간에서 자르면 뜻이 무너지므로 끊지 않는다
_CODE_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_TABLE_ROW = re.compile(r"\s*\|.*\|\s*")

DOC_TYPE_LABELS = {"runbook": "런북", "sla": "SLA 계약서"}


@dataclass(frozen=True)
class Chunk:
    chunk_no: int
    content: str        # 헤더가 붙은 본문 (임베딩 입력)
    section_path: str   # 예: "B사 SLA 계약서 > 제5조 서비스 크레딧". 제목이 없는 문서는 빈 값
    section: str        # 절 경로의 마지막 제목. 기존 검색 결과의 section 칸이다


@dataclass(frozen=True)
class ChunkHeader:
    """조각 앞에 붙이는 문서 맥락. 조각만 보고도 어느 문서의 어디인지 알 수 있어야 한다."""

    title: str
    scope: str      # 고객사 이름 또는 "MSP 공통"
    doc_type: str

    def render(self, section_path: str) -> str:
        header = f"[문서] {self.title} | {self.scope} | {DOC_TYPE_LABELS.get(self.doc_type, self.doc_type)}"
        return header + (f" | 절 {section_path}" if section_path.strip() else "")


def estimate_tokens(text: str | None) -> int:
    """한글·자모·한자 0.7, 그 외 4자당 1. 실제 토크나이저 대신 쓰는 추정값이다."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "가" <= ch <= "힣" or "㄰" <= ch <= "㆏" or "一" <= ch <= "鿿")
    return math.ceil(cjk * 0.7 + (len(text) - cjk) / 4.0)


def chunk_document(parsed: ParsedDocument, header: ChunkHeader) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, body in _split_by_headings(parsed):
        for piece in _split_by_length(body):
            if not piece.strip():
                continue
            last = path.rsplit(" > ", 1)[-1] if path else ""
            chunks.append(Chunk(len(chunks), header.render(path) + "\n\n" + piece.strip(), path, last))
    return chunks


def _split_by_headings(parsed: ParsedDocument) -> list[tuple[str, str]]:
    """(절 경로, 절 본문) 목록. 제목 줄 자신도 절 본문에 들어간다. 제목 줄뿐인 절은 버린다."""
    text, headings = parsed.text, parsed.headings
    if not headings:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    if headings[0].offset > 0:
        sections.append(("", text[: headings[0].offset]))   # 첫 제목 앞의 도입부
    stack: list[str | None] = [None] * 7
    for i, heading in enumerate(headings):
        stack[heading.level] = heading.title
        for deeper in range(heading.level + 1, len(stack)):
            stack[deeper] = None
        end = headings[i + 1].offset if i + 1 < len(headings) else len(text)
        body = text[heading.offset:end]
        rest = body.split("\n", 1)[1] if "\n" in body else ""
        if rest.strip():   # "# 문서 제목" 바로 뒤에 "## 조항"이 오면 제목 줄만 남은 조각이 생기지 않게 한다
            sections.append((" > ".join(t for t in stack[1:] if t is not None), body))
    return sections


def _split_by_length(body: str) -> list[str]:
    if estimate_tokens(body) <= MAX_TOKENS:
        return [body]
    lines = body.split("\n")
    pieces: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0
    inside_code = False
    for line in lines:
        was_inside = inside_code
        if _CODE_FENCE.match(line):
            inside_code = not inside_code
        unbreakable = was_inside or inside_code or _TABLE_ROW.fullmatch(line) is not None
        line_tokens = estimate_tokens(line)
        if not unbreakable and buffer and buffer_tokens + line_tokens > TARGET_TOKENS:
            pieces.append("\n".join(buffer))
            buffer = _tail_for_overlap(buffer)
            buffer_tokens = estimate_tokens("\n".join(buffer))
        buffer.append(line)
        buffer_tokens += line_tokens
    if buffer:
        pieces.append("\n".join(buffer))
    return pieces


def _tail_for_overlap(lines: list[str]) -> list[str]:
    """겹침 구간. 뒤에서부터 누적 토큰이 목표에 닿을 때까지 줄을 가져온다."""
    tail: list[str] = []
    tokens = 0
    for line in reversed(lines):
        if tokens >= OVERLAP_TOKENS:
            break
        tail.insert(0, line)
        tokens += estimate_tokens(line)
    return tail


_CLAUSE = re.compile(r"^(RB-STD-\d{2})(?![\w-])")


def clause_numbers(parsed: ParsedDocument) -> list[str]:
    """제목에서 찾은 공통 표준 런북 조항 번호 (나온 순서, 중복 포함). 검사는 documents.py가 한다."""
    return [m.group(1) for h in parsed.headings if (m := _CLAUSE.match(h.title))]

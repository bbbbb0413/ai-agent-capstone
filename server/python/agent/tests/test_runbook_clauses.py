"""공통 표준 런북(data/documents/_common/runbook-standard.md)과 코드·프롬프트가 어긋나지 않는지 본다.

조항별 조치 규칙은 런북, prompts/propose.md의 장애 형태 판정표, action_guards.CLAUSE_ACTIONS 세 곳에 있다.
한 곳을 고치고 나머지를 빠뜨리면 모델이 없는 조항을 고르거나 조치 검사가 엉뚱하게 거부한다.
"""

import re
import unittest
from pathlib import Path

from agent import action_guards, signals

RUNBOOK = Path(__file__).resolve().parents[4] / "data" / "documents" / "_common" / "runbook-standard.md"
PROPOSE = Path(__file__).resolve().parents[1] / "prompts" / "propose.md"
# 장애 형태가 아니라 "조치를 할 수 없을 때"의 조항이라 판정표에 없다
NOT_A_FAULT_SHAPE = {"RB-STD-06"}
ACTIONS = {"restart_pods", "scale_deployment", "rollback_deployment", "adjust_autoscaler"}


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (RB-STD-0\d)\b.*$", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def mentioned(text: str) -> set[str]:
    return set(re.findall(r"`(\w+)`", text)) & ACTIONS


@unittest.skipUnless(RUNBOOK.exists(), "저장소 밖에서 실행: 런북 문서가 없다")
class RunbookClauseTests(unittest.TestCase):
    def setUp(self):
        self.sections = sections(RUNBOOK.read_text(encoding="utf-8"))

    def test_code_knows_exactly_the_documented_clauses(self):
        self.assertEqual(set(self.sections), set(action_guards.CLAUSE_ACTIONS))

    def prescribed(self, clause: str) -> set[str]:
        """조항의 '- 조치:' 줄에 적힌 조치."""
        return mentioned("\n".join(l for l in self.sections[clause].splitlines() if l.startswith("- 조치:")))

    def test_clause_actions_match_the_document(self):
        for clause, body in self.sections.items():
            with self.subTest(clause=clause):
                # 다른 조항의 조치를 검토하라고 넘기는 경우 (RB-STD-01: "직전 변경이 없으면 RB-STD-02의 재시작을 검토한다")
                handed = set(re.findall(r"(RB-STD-0\d)의 \S+을 검토", body))
                expected = self.prescribed(clause).union(*(self.prescribed(c) for c in handed))
                self.assertEqual(action_guards.CLAUSE_ACTIONS[clause], expected)


def judgment_table(text: str) -> list[tuple[str, str]]:
    """propose.md 판정표의 (로그 신호 칸, 장애 형태 칸). 머리줄과 구분줄은 뺀다."""
    rows = [line.strip().strip("|").split("|") for line in text.splitlines() if line.strip().startswith("| ")]
    return [(cells[1], cells[2]) for cells in rows if len(cells) == 3 and "RB-STD" in cells[2]]


@unittest.skipUnless(RUNBOOK.exists(), "저장소 밖에서 실행: 런북 문서가 없다")
class JudgmentTableTests(unittest.TestCase):
    def setUp(self):
        self.runbook = set(sections(RUNBOOK.read_text(encoding="utf-8")))
        self.rows = judgment_table(PROPOSE.read_text(encoding="utf-8"))

    def test_table_names_every_fault_shape_of_the_runbook(self):
        clauses = {c for _, shape in self.rows for c in re.findall(r"RB-STD-0\d", shape)}
        self.assertEqual(clauses, self.runbook - NOT_A_FAULT_SHAPE)

    def test_table_uses_exactly_the_signals_code_counts(self):
        names = {n for cell, _ in self.rows for n in re.findall(r"`(\w+)`", cell)}
        self.assertEqual(names, set(signals.LOG_SIGNALS))


if __name__ == "__main__":
    unittest.main()

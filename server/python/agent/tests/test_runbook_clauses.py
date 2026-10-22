"""graph.CLAUSE_ACTIONS가 공통 표준 런북(data/documents/_common/runbook-standard.md)과 어긋나지 않는지 본다.

조항별 조치 규칙은 문서, prompts/propose.md, 코드 세 곳에 있다. 문서를 고치고 코드를 빠뜨리면 조치 검사가 엉뚱하게 거부한다.
"""

import re
import unittest
from pathlib import Path

from agent import graph

RUNBOOK = Path(__file__).resolve().parents[4] / "data" / "documents" / "_common" / "runbook-standard.md"
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
        self.assertEqual(set(self.sections), set(graph.CLAUSE_ACTIONS))

    def prescribed(self, clause: str) -> set[str]:
        """조항의 '- 조치:' 줄에 적힌 조치."""
        return mentioned("\n".join(l for l in self.sections[clause].splitlines() if l.startswith("- 조치:")))

    def test_clause_actions_match_the_document(self):
        for clause, body in self.sections.items():
            with self.subTest(clause=clause):
                # 다른 조항의 조치를 검토하라고 넘기는 경우 (RB-STD-01: "직전 변경이 없으면 RB-STD-02의 재시작을 검토한다")
                handed = set(re.findall(r"(RB-STD-0\d)의 \S+을 검토", body))
                expected = self.prescribed(clause).union(*(self.prescribed(c) for c in handed))
                self.assertEqual(graph.CLAUSE_ACTIONS[clause], expected)

if __name__ == "__main__":
    unittest.main()

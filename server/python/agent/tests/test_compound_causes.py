"""겹친 장애: 진단이 적은 원인 후보 읽기, 처리할 원인 검사, 처리한 원인 기록 (SPEC-compound-faults 4장).

원인 순위는 모델이 정한다. 코드는 그 결과를 읽어 명백한 위반만 막는다.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from agent import action_guards, graph
from fixtures import OBS

COMPOUND = """이상 시작: 05:00
근거: 에러율 0.93, 503 worker pool saturated 412건
직전 변경: 없음
원인 후보:
1) RB-STD-02 작업 스레드 고갈 — worker_pool_saturated 412건, 에러율 0.93 (증상: RB-STD-03)
2) RB-STD-04 외부 의존 지연 — external_api_timeout 12건
처리할 원인: RB-STD-02
병목: 앱"""


def call(name: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "id": "c1", "args": args}])


def act(action: str) -> AIMessage:
    return call("business_execute_runbook_action", {"action": action, "service": "order-api", "reason": "x"})


def escalate(category: str, **args) -> AIMessage:
    return call("business_escalate_incident", {"category": category, "reason": "x", **args})


NOTICE = call("business_send_customer_notice", {"message": "x"})


class ParseCausesTests(unittest.TestCase):
    def test_candidates_in_written_order_and_the_target(self):
        self.assertEqual(graph.parse_causes(COMPOUND), (["RB-STD-02", "RB-STD-04"], "RB-STD-02"))

    def test_symptom_clause_in_a_candidate_line_is_not_a_candidate(self):
        causes, _ = graph.parse_causes(COMPOUND)
        self.assertNotIn("RB-STD-03", causes)   # "(증상: RB-STD-03)"은 후보 줄 머리가 아니다

    def test_broken_format_and_undetermined_yield_nothing(self):
        self.assertEqual(graph.parse_causes("장애 형태: RB-STD-02 작업 스레드 고갈"), ([], None))
        self.assertEqual(graph.parse_causes("원인 미확정: 도구로 근거를 모으지 못했다"), ([], None))
        only_target = "원인 후보:\n처리할 원인: RB-STD-05"
        self.assertEqual(graph.parse_causes(only_target), ([], "RB-STD-05"))


class TargetGuardTests(unittest.TestCase):
    FACTS = {"facts": [{"clause": "RB-STD-02", "signal": "worker_pool_saturated", "signal_count": 412, "evidence": True},
                       {"clause": "RB-STD-04", "signal": "external_api_timeout", "signal_count": 0, "evidence": False,
                        "dependency_status": "HEALTHY"}]}

    def test_target_without_evidence_is_sent_back(self):
        self.assertIn("근거가 없다", action_guards.target_without_evidence("RB-STD-04", self.FACTS))
        self.assertIsNone(action_guards.target_without_evidence("RB-STD-02", self.FACTS))
        self.assertIsNone(action_guards.target_without_evidence("RB-STD-07", self.FACTS))   # 사실 표에 없는 조항
        self.assertIsNone(action_guards.target_without_evidence(None, self.FACTS))

    def test_handled_cause_gets_no_second_app_action_but_may_be_escalated(self):
        handled = [{"clause": "RB-STD-03", "action": "adjust_autoscaler"}]
        self.assertIn("이미", action_guards.target_already_handled(act("scale_deployment"), "RB-STD-03", handled))
        # a-surge-max: 최대치를 올려도 회복되지 않으면 구조상 막혔다는 에스컬레이션은 된다
        blocked = escalate("ACTION_BLOCKED", required_action="adjust_autoscaler", blocked_by="STRUCTURE")
        self.assertIsNone(action_guards.target_already_handled(blocked, "RB-STD-03", handled))
        self.assertIsNone(action_guards.target_already_handled(act("restart_pods"), "RB-STD-02", handled))

    def test_action_must_belong_to_the_target(self):
        cases = {
            # (처리할 원인, 조치) → 맞나
            ("RB-STD-02", "restart"): (act("restart_pods"), True),
            ("RB-STD-02", "notice"): (NOTICE, False),
            ("RB-STD-02", "dependency"): (escalate("DEPENDENCY", dependency="postgres"), False),
            ("RB-STD-04", "notice"): (NOTICE, True),
            ("RB-STD-04", "restart"): (act("restart_pods"), False),
            ("RB-STD-04", "blocked"): (escalate("ACTION_BLOCKED", required_action="restart_pods"), False),
            ("RB-STD-05", "dependency"): (escalate("DEPENDENCY", dependency="postgres"), True),
            ("RB-STD-05", "notice"): (NOTICE, False),
            ("RB-STD-01", "rollback"): (act("rollback_deployment"), True),
            ("RB-STD-01", "blocked"): (escalate("ACTION_BLOCKED", required_action="rollback_deployment"), True),
            ("RB-STD-03", "autoscaler"): (act("adjust_autoscaler"), True),
            ("RB-STD-03", "p1"): (escalate("P1"), True),
            ("RB-STD-07", "unknown"): (escalate("CAUSE_UNKNOWN"), True),
        }
        for (target, label), (ai, ok) in cases.items():
            with self.subTest(target=target, action=label):
                problem = action_guards.target_mismatch(ai, target, recent_change=True)
                self.assertEqual(problem is None, ok, problem)

    def test_restart_does_not_fix_a_pool_after_a_recent_change(self):
        self.assertIsNotNone(action_guards.target_mismatch(act("restart_pods"), "RB-STD-01", recent_change=True))
        self.assertIsNone(action_guards.target_mismatch(act("restart_pods"), "RB-STD-01", recent_change=False))

    def test_without_a_target_nothing_new_is_checked(self):
        for ai in (act("restart_pods"), NOTICE, escalate("DEPENDENCY")):
            self.assertIsNone(action_guards.target_mismatch(ai, None, True))

    def test_blocked_escalation_check_uses_the_target_when_signals_overlap(self):
        # 풀 고갈과 작업 스레드 고갈 신호가 같이 있으면 코드로는 하나를 정할 수 없다 (signal_clause None)
        both = {"metrics": {"error_rate": 0.6}, "logs": {"connection_pool_exhausted": 50, "worker_pool_saturated": 40}}
        self.assertIsNone(action_guards.signal_clause(both))
        wrong = escalate("ACTION_BLOCKED", required_action="restart_pods", blocked_by="CONTRACT")
        self.assertIsNone(action_guards.blocked_action_mismatch(wrong, both, True))
        self.assertIn("RB-STD-01", action_guards.blocked_action_mismatch(wrong, both, True, "RB-STD-01"))

    def test_first_problem_runs_the_target_checks_first(self):
        context = {"services": [{"name": "order-api", "supportedActions": ["restart_pods"]}]}
        problem = action_guards.first_problem(NOTICE, context, self.FACTS, False, "RB-STD-02")
        self.assertIn("처리할 원인은 RB-STD-02", problem)
        self.assertIsNone(action_guards.first_problem(act("restart_pods"), context, self.FACTS, False, "RB-STD-02"))


class HandledTests(unittest.IsolatedAsyncioTestCase):
    STATE = {"target_clause": "RB-STD-02", "signals": {"logs": {"worker_pool_saturated": 400}},
             "proposed_action": {"tool": "business_execute_runbook_action",
                                 "args": {"action": "restart_pods", "service": "order-api"}},
             "tool_counts": {}, "evidence": [], "context": {"services": [{"name": "order-api", "observability": OBS}]}}

    async def test_executed_app_action_is_recorded_with_the_signal_count(self):
        result = ToolMessage(content=json.dumps({"status": "EXECUTED"}), status="success", tool_call_id="t")
        box = SimpleNamespace(call=AsyncMock(return_value=result))
        update = await graph.execute(dict(self.STATE), SimpleNamespace(context=SimpleNamespace(toolbox=box)))
        [done] = update["handled"]
        self.assertEqual((done["clause"], done["action"], done["signal"], done["signal_count_before"]),
                         ("RB-STD-02", "restart_pods", "worker_pool_saturated", 400))

    async def test_escalation_and_failed_action_are_not_recorded(self):
        for status, tool in (("QUEUED", "business_escalate_incident"), ("FAILED", "business_execute_runbook_action")):
            result = ToolMessage(content=json.dumps({"status": status}), status="success", tool_call_id="t")
            box = SimpleNamespace(call=AsyncMock(return_value=result))
            state = {**self.STATE, "proposed_action": {"tool": tool, "args": {"category": "P1", "reason": "x"}}}
            update = await graph.execute(state, SimpleNamespace(context=SimpleNamespace(toolbox=box)))
            self.assertNotIn("handled", update, status)

    async def recount(self, after: int) -> dict:
        at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(timespec="seconds")
        state = {**self.STATE, "handled": [{"clause": "RB-STD-02", "action": "restart_pods",
                                            "signal": "worker_pool_saturated", "signal_count_before": 400, "at": at}]}
        details = AsyncMock(return_value={"worker_pool_saturated": {"count": after, "first_seen": None}})
        with patch("agent.signals.log_signal_details", new=details):
            handled = await graph._recount_handled(None, state, OBS, "order-api")
        self.assertEqual(details.await_args.args[3], datetime.fromisoformat(at))   # 조치 뒤 구간만 센다
        return handled[-1]

    async def test_signal_that_dropped_is_cleared_and_one_that_stayed_is_not(self):
        cleared = await self.recount(after=0)
        self.assertEqual((cleared["signal_count_after"], cleared["cleared"]), (0, True))
        stayed = await self.recount(after=390)
        self.assertEqual((stayed["signal_count_after"], stayed["cleared"]), (390, False))

    def test_final_escalation_names_the_causes_left_unhandled(self):
        state = {"incident_id": 1, "attempts": 1, "causes": ["RB-STD-02", "RB-STD-04", "RB-STD-05"],
                 "handled": [{"clause": "RB-STD-02"}, {"clause": "RB-STD-04"}],
                 "proposed_action": {"args": {"action": "restart_pods", "service": "order-api"}}}
        args = graph._escalation_args(state)
        self.assertEqual(args["category"], "NOT_RECOVERED")
        self.assertIn("처리하지 못한 원인 후보: RB-STD-05", args["reason"])
        self.assertNotIn("처리하지 못한", graph._escalation_args({**state, "causes": []})["reason"])


if __name__ == "__main__":
    unittest.main()

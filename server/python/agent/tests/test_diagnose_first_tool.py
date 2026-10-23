"""진단 첫 턴 도구 강제와 근거 없음 처리를 실제 LLM·MCP 호출 없이 검증한다 (docs/기능별-변경사항.md T8)."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from agent import graph
from fixtures import OBS

NO_EVIDENCE = "원인 미확정: 도구로 근거를 모으지 못했다"


def tool_call(call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "grafana_query_prometheus", "args": {"expr": "up"}, "id": call_id}])


def fake_model(*replies: AIMessage) -> SimpleNamespace:
    return SimpleNamespace(ainvoke=AsyncMock(side_effect=list(replies)))


def runtime_with(tool_status: str = "success") -> SimpleNamespace:
    result = ToolMessage(content="<tool_output>\n[]\n</tool_output>", status=tool_status, tool_call_id="c1")
    box = SimpleNamespace(for_node=lambda node: [], call=AsyncMock(return_value=result))
    return SimpleNamespace(context=SimpleNamespace(toolbox=box, check_time=lambda: None))


def models(required: SimpleNamespace, normal: SimpleNamespace):
    """harness.chat_model 대역. tool_choice="required"로 만든 모델과 일반 모델을 구분해 돌려준다."""
    return patch("agent.graph.harness.chat_model",
                 side_effect=lambda tools, *a, tool_choice=None, **k: required if tool_choice == "required" else normal)


STATE = {"turns": 0, "tool_counts": {}, "incident_id": 1, "evidence": [], "question": "order-api 에러율 20% 초과",
         "context": {"incident": {"service": "order-api"},
                     "services": [{"name": "order-api", "observability": OBS}]}}


class FirstToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_t8_1_only_first_turn_requires_a_tool(self):
        required, normal = fake_model(tool_call()), fake_model(AIMessage("HikariPool 고갈"))
        with models(required, normal) as chat_model:
            new, *_ = await graph._tool_loop("diagnose", STATE, runtime_with(), [], 8, force_first_tool=True)
        self.assertEqual(required.ainvoke.await_count, 1)
        self.assertEqual(normal.ainvoke.await_count, 1)
        self.assertEqual(new[-1].content, "HikariPool 고갈")

        required, normal = fake_model(), fake_model(tool_call(), AIMessage("HikariPool 고갈"))
        with models(required, normal) as chat_model:
            await graph._tool_loop("diagnose", STATE, runtime_with(), [], 8, force_first_tool=False)
        self.assertNotIn("required", [c.kwargs.get("tool_choice") for c in chat_model.call_args_list])
        self.assertEqual(normal.ainvoke.await_count, 2)


class NoEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def diagnose(self, model: SimpleNamespace, tool_status: str = "success") -> dict:
        logged = {"tool": "grafana_query_loki_logs", "args": {"startRfc3339": "2026-10-20T00:00:00Z"},
                  "status": "success", "summary": ""}
        with models(model, model), patch("agent.signals.recent_log_patterns", AsyncMock(return_value=("", logged))):
            update = await graph.diagnose(dict(STATE), runtime_with(tool_status))
        return {**STATE, **update}

    async def test_t8_2_plan_only_or_failed_tools_go_straight_to_report(self):
        plan_only = await self.diagnose(fake_model(AIMessage("1. 에러율 확인 2. 로그 확인 3. 커넥션 풀 확인")))
        self.assertEqual(plan_only["findings"], NO_EVIDENCE)
        self.assertEqual(graph.route_diagnose(plan_only), "report")

        all_failed = await self.diagnose(fake_model(tool_call(), AIMessage("DB 문제로 보인다")), tool_status="error")
        self.assertEqual(all_failed["findings"], NO_EVIDENCE)
        self.assertEqual(graph.route_diagnose(all_failed), "report")

    async def test_t8_3_successful_tool_keeps_the_final_answer(self):
        answer = "HikariPool 고갈로 커넥션 대기 시간 초과"
        result = await self.diagnose(fake_model(tool_call(), AIMessage(answer)))
        self.assertEqual(result["findings"], answer)
        self.assertEqual(graph.route_diagnose(result), "retrieve_runbook")


if __name__ == "__main__":
    unittest.main()

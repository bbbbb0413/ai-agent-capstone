"""서비스 카탈로그의 관측 정의로 만든 진단·회복 확인 쿼리 (SPEC-limitations-fixes 4단계)."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from agent import graph, signals
from fixtures import OBS

# 4단계 전에 상수로 두었던 쿼리. 카탈로그 값으로 만든 쿼리는 여기에 서비스 조건만 더해진다
BEFORE = {
    "error_rate": '(sum(rate(demo_orders_seconds_count{outcome="error"}[1m])) or vector(0))'
                  ' / sum(rate(demo_orders_seconds_count[1m]))',
    "p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[1m])))',
    "requests_per_second": 'sum(rate(demo_orders_seconds_count[1m]))',
}


def two_services(acted_obs=OBS):
    return {"services": [{"name": "order-api", "observability": acted_obs},
                         {"name": "payment-api", "observability": OBS}]}


class QueryTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_queries_are_the_old_queries_with_a_service_condition(self):
        queries = signals.diagnose_queries(OBS, "order-api")
        expected = {name: expr.replace('_count{outcome="error"}', '_count{service="order-api", outcome="error"}')
                    .replace("_count[", '_count{service="order-api"}[').replace("_bucket[", '_bucket{service="order-api"}[')
                    for name, expr in BEFORE.items()}
        self.assertEqual(queries, expected)
        verify = signals.verify_queries(OBS, "order-api")
        self.assertEqual(verify, {k: v.replace("[1m]", "[15s]") for k, v in expected.items() if k != "requests_per_second"})

    def test_log_query_is_the_old_selector_with_a_service_condition(self):
        self.assertEqual(signals.log_query(OBS, "order-api"), '{app="demo-app", service="order-api", level=~"ERROR|WARN"}')

    def test_observability_needs_a_known_service_with_a_definition(self):
        context = {"services": [{"name": "order-api", "observability": OBS},
                                {"name": "settlement-batch", "observability": None}]}
        self.assertEqual(signals.observability(context, "order-api"), OBS)
        for service in ("settlement-batch", "unknown", None):
            self.assertIsNone(signals.observability(context, service))

    async def test_signal_without_a_catalog_phrase_is_left_out(self):
        rows = [{"line": "slow query took 3000ms", "labels": {"level": "WARN"}}]
        box = SimpleNamespace(call=AsyncMock(return_value=ToolMessage(content=json.dumps(rows), status="success",
                                                                      tool_call_id="l")))
        obs = {**OBS, "logSignals": {"slow_query": "slow query"}}
        _, logged = await signals.recent_log_patterns(box, {}, None, None, obs, "order-api")
        self.assertEqual(logged["signals"], {"slow_query": 1})   # 문구가 없는 신호는 0건이 아니라 모른다
        self.assertEqual(box.call.await_args.args[2]["logql"], signals.log_query(obs, "order-api"))


class LogWindowTests(unittest.TestCase):
    """같은 서비스의 직전 인시던트가 닫힌 뒤부터 로그를 본다 (SPEC-e2e-speed ②)."""
    NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)

    def test_previous_resolution_cuts_the_lead(self):
        incident = {"startedAt": "2026-10-23T08:59:00Z", "previousResolvedAt": "2026-10-23T08:57:30Z"}
        self.assertEqual(signals.log_window_start(self.NOW, incident), datetime(2026, 10, 23, 8, 57, 30, tzinfo=timezone.utc))
        # 직전 인시던트가 2분보다 먼저 닫혔으면 지금과 같다
        older = {**incident, "previousResolvedAt": "2026-10-23T08:50:00Z"}
        self.assertEqual(signals.log_window_start(self.NOW, older), self.NOW - timedelta(minutes=3))
        # 이 인시던트보다 늦게 닫힌 값(시계 차이 등)은 쓰지 않는다
        later = {**incident, "previousResolvedAt": "2026-10-23T08:59:30Z"}
        self.assertEqual(signals.log_window_start(self.NOW, later), self.NOW - timedelta(minutes=3))
        self.assertEqual(signals.log_window_start(self.NOW, {"startedAt": "2026-10-23T08:59:00Z"}),
                         self.NOW - timedelta(minutes=3))

    def test_previous_resolved_at_is_the_latest_earlier_incident_of_the_same_service(self):
        incident = {"id": 9, "service": "order-api"}
        resolved = [{"id": 7, "service": "order-api", "resolvedAt": "2026-10-23T08:40:00Z"},
                    {"id": 8, "service": "order-api", "resolvedAt": "2026-10-23T08:57:30Z"},
                    {"id": 6, "service": "cart-api", "resolvedAt": "2026-10-23T08:58:30Z"},
                    {"id": 10, "service": "order-api", "resolvedAt": "2026-10-23T08:59:50Z"}]
        self.assertEqual(graph._previous_resolved_at(incident, resolved), "2026-10-23T08:57:30Z")
        self.assertIsNone(graph._previous_resolved_at(incident, "실패"))
        self.assertIsNone(graph._previous_resolved_at(incident, []))


class DiagnoseServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_without_observability_ends_undetermined_without_queries(self):
        box = SimpleNamespace(call=AsyncMock())
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=box, check_time=lambda: None))
        state = {"turns": 0, "tool_counts": {}, "incident_id": 1, "evidence": [], "question": "q",
                 "context": {"incident": {"service": "settlement-batch"},
                             "services": [{"name": "settlement-batch", "observability": None}]}}
        result = await graph.diagnose(state, runtime)
        self.assertTrue(result["findings"].startswith("원인 미확정: settlement-batch의 관측 정의"), result["findings"])
        self.assertEqual(graph.route_diagnose({**state, **result}), "report")
        box.call.assert_not_awaited()

    def test_question_without_incident_uses_the_only_open_incident_service(self):
        state = {"question": "왜 느려?", "context": {"open_incidents": [{"service": "order-api"}, {"service": "order-api"}]}}
        self.assertEqual(graph._diagnosed_service(state), "order-api")
        both = {"question": "왜 느려?", "context": {"open_incidents": [{"service": "order-api"}, {"service": "payment-api"}]}}
        self.assertIsNone(graph._diagnosed_service(both))


class VerifyServiceTests(unittest.IsolatedAsyncioTestCase):
    """한 고객사의 두 서비스 중 조치한 서비스만 회복 확인에 쓴다."""

    async def verify(self, acted: str, context: dict) -> tuple[dict, list[str]]:
        asked: list[str] = []

        async def metric(box, expr, counts, node="verify"):
            asked.append(expr)
            # payment-api는 계속 나쁘고 order-api는 회복했다
            return 0.5 if 'service="payment-api"' in expr else 0.0

        model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="RECOVERED")))
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=None, settle=AsyncMock(), check_time=lambda: None))
        state = {"task_id": "t", "tool_counts": {}, "turns": 0, "evidence": [], "findings": "x", "context": context,
                 "proposed_action": {"tool": "business_execute_runbook_action", "args": {"service": acted}}}
        with patch("agent.signals.metric", new=metric), patch("agent.graph.harness.chat_model", return_value=model), \
                patch("agent.graph.backend.emit", new=AsyncMock()):
            return await graph.verify(state, runtime), asked

    async def test_only_the_acted_service_is_measured(self):
        result, asked = await self.verify("order-api", two_services())
        self.assertTrue(result["recovered"])
        self.assertTrue(asked and all('service="order-api"' in e and "payment-api" not in e for e in asked), asked)

        self.assertEqual(json.loads(result["evidence"][-1]["summary"])["error_rate"], 0.0)

        result, asked = await self.verify("payment-api", two_services())
        self.assertTrue(asked and all('service="payment-api"' in e for e in asked), asked)
        self.assertEqual(json.loads(result["evidence"][-1]["summary"])["error_rate"], 0.5)

    async def test_acted_service_without_observability_is_not_recovered(self):
        result, asked = await self.verify("order-api", two_services(acted_obs=None))
        self.assertEqual(asked, [])
        self.assertFalse(result["recovered"])   # 모델은 RECOVERED라 해도 잴 수 없으면 회복이 아니다
        self.assertIn("관측 정의가 없어", result["verification"])


if __name__ == "__main__":
    unittest.main()

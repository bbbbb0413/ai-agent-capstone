"""겹친 장애의 원인 후보별 사실 (SPEC-compound-faults 3장). 코드는 사실만 모으고 순위는 정하지 않는다."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from agent import signals
from fixtures import OBS

END = datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc)
START = END - timedelta(minutes=10)


def message(body, status="success"):
    return ToolMessage(content=json.dumps(body) if not isinstance(body, str) else body, status=status,
                       tool_call_id="c")


def series(*points):
    """Loki range 집계 결과. points는 (구간 끝 시각, 건수)."""
    return {"data": [{"values": [{"timestamp": f"{t.timestamp():.3f}", "value": v} for t, v in points]}]}


class LogSignalDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def details(self, answers: dict[str, object], obs=OBS):
        """answers: 신호 문구 → 결과. 없는 문구는 빈 결과(0건)다."""
        asked = []

        async def call(node, name, args, counts, **kwargs):
            asked.append(args)
            phrase = next((p for p in obs["logSignals"].values() if json.dumps(p) in args["logql"]), None)
            answer = answers.get(phrase, {"data": []})
            return answer if isinstance(answer, ToolMessage) else message(answer)

        box = SimpleNamespace(call=AsyncMock(side_effect=call))
        return await signals.log_signal_details(box, obs, "order-api", START, END), asked

    async def test_count_is_the_sum_of_buckets_and_first_seen_is_the_first_bucket_start(self):
        hang = series((END - timedelta(seconds=60), 40), (END - timedelta(seconds=55), 80), (END, 75))
        found, asked = await self.details({"503 worker pool saturated": hang})
        self.assertEqual(found["worker_pool_saturated"],
                         {"count": 195, "first_seen": (END - timedelta(seconds=65)).isoformat(timespec="seconds")})
        self.assertEqual(found["external_api_timeout"], {"count": 0, "first_seen": None})
        self.assertEqual(len(asked), len(signals.LOG_SIGNALS))   # 신호마다 한 번씩 센다 (200줄 요약에 묻히지 않게)
        args = asked[0]
        self.assertEqual((args["queryType"], args["stepSeconds"]), ("range", signals.SIGNAL_STEP_SECONDS))
        self.assertTrue(args["logql"].startswith("sum(count_over_time({" ) and 'service="order-api"' in args["logql"])

    async def test_two_signals_at_once_are_both_counted(self):
        found, _ = await self.details({"503 worker pool saturated": series((END, 400)),
                                       "payment API timeout": series((END - timedelta(seconds=30), 3), (END, 9))})
        self.assertEqual(found["worker_pool_saturated"]["count"], 400)
        self.assertEqual(found["external_api_timeout"]["count"], 12)

    async def test_failed_query_and_signal_without_phrase_are_left_out(self):
        obs = {**OBS, "logSignals": {"slow_query": "slow query", "worker_pool_saturated": "503 worker pool saturated"}}
        found, asked = await self.details({"slow query": message("boom", status="error")}, obs)
        self.assertEqual(set(found), {"worker_pool_saturated"})   # 실패는 0건이 아니라 모른다
        self.assertEqual(len(asked), 2)


class DependencyHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_dependency_of_the_service_is_looked_up(self):
        context = {"services": [{"name": "order-api", "dependencies": [
            {"name": "postgres", "kind": "database", "owner": "tenant"},
            {"name": "payment-gateway", "kind": "external_api", "owner": "third_party"}]},
            {"name": "other", "dependencies": [{"name": "redis", "kind": "database"}]}]}

        async def call(node, name, args, counts, **kwargs):
            if args["dependency"] == "postgres":
                return message({"status": "DEGRADED", "p95Seconds": 1.2})
            return message("down", status="error")

        box = SimpleNamespace(call=AsyncMock(side_effect=call))
        found = await signals.dependency_health(box, context, "order-api")
        self.assertEqual([(d["name"], d["status"], d["p95Seconds"]) for d in found],
                         [("postgres", "DEGRADED", 1.2), ("payment-gateway", "UNKNOWN", None)])
        self.assertEqual([c.args[1] for c in box.call.await_args_list], ["business_get_dependency_health"] * 2)


class CauseFactsTests(unittest.TestCase):
    METRICS = {"error_rate": 0.9, "p95_seconds": 2.0}

    def by_clause(self, facts):
        return {f["clause"]: f for f in facts}

    def test_single_cause_has_evidence_and_others_do_not(self):
        logs = {name: {"count": 0, "first_seen": None} for name in signals.LOG_SIGNALS}
        logs["worker_pool_saturated"] = {"count": 412, "first_seen": "2026-10-23T05:00:00+00:00"}
        facts = self.by_clause(signals.cause_facts(logs, [], self.METRICS))
        self.assertEqual(set(facts), set(signals.LOG_SIGNALS.values()))
        self.assertTrue(facts["RB-STD-02"]["evidence"])
        self.assertEqual(facts["RB-STD-02"]["first_seen"], "2026-10-23T05:00:00+00:00")
        self.assertEqual([c for c, f in facts.items() if f["evidence"]], ["RB-STD-02"])

    def test_compound_causes_both_have_evidence_and_no_order_is_imposed(self):
        logs = {"worker_pool_saturated": {"count": 400, "first_seen": "a"},
                "external_api_timeout": {"count": 12, "first_seen": "b"}}
        facts = signals.cause_facts(logs, [], self.METRICS)
        self.assertEqual([f["clause"] for f in facts if f["evidence"]], ["RB-STD-02", "RB-STD-04"])
        self.assertEqual([f["clause"] for f in facts], ["RB-STD-02", "RB-STD-04"])   # 조항 순서 그대로, 순위가 아니다

    def test_minimum_signal_count_is_the_noise_floor(self):
        for count, expected in ((signals.MIN_SIGNAL_COUNT - 1, False), (signals.MIN_SIGNAL_COUNT, True)):
            facts = signals.cause_facts({"slow_query": {"count": count, "first_seen": None}}, [], self.METRICS)
            self.assertEqual(facts[0]["evidence"], expected, count)

    def test_unhealthy_dependency_is_evidence_without_logs_and_share_is_against_request_p95(self):
        deps = [{"name": "postgres", "kind": "database", "status": "DEGRADED", "p95Seconds": 1.5},
                {"name": "payment-gateway", "kind": "external_api", "status": "HEALTHY", "p95Seconds": 0.05}]
        facts = self.by_clause(signals.cause_facts({"slow_query": {"count": 0, "first_seen": None}}, deps, self.METRICS))
        self.assertTrue(facts["RB-STD-05"]["evidence"])
        self.assertEqual((facts["RB-STD-05"]["dependency"], facts["RB-STD-05"]["dependency_share"]), ("postgres", 0.75))
        self.assertFalse(facts["RB-STD-04"]["evidence"])   # 신호를 셀 수 없고 의존 대상이 정상이다
        self.assertIsNone(facts["RB-STD-04"]["signal_count"])
        no_p95 = signals.cause_facts({}, deps, {"p95_seconds": None})
        self.assertTrue(all(f["dependency_share"] is None for f in no_p95))

    def test_signal_that_cannot_be_counted_without_a_dependency_has_no_row(self):
        self.assertEqual(signals.cause_facts({}, [], self.METRICS), [])


if __name__ == "__main__":
    unittest.main()

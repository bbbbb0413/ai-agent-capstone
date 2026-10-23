"""Jev 호출 없이 장애 형태 판정의 입력 변환, 실패 처리, 조치 제안 연결을 검증한다 (docs/기능별-변경사항.md T14)."""

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from agent import graph, jev, signals
from fixtures import OBS


class BuildStateTests(unittest.TestCase):
    def test_numbers_become_threshold_words(self):  # T14-1
        high = jev.build_state({
            "metrics": {"error_rate": 0.05, "p95_seconds": 1.2},
            "logs": {"connection_pool_exhausted": 12, "slow_query": 0},
            "recent_change": True,
        })
        self.assertEqual(high["5xx 에러율"], "높음 (5.0%, 기준 5% 이상)")
        self.assertEqual(high["p95 응답시간"], "느림 (1.20초, 기준 1초 이상)")
        self.assertEqual(high["로그 신호"][jev.LOG_LABELS["connection_pool_exhausted"]], "12건")
        self.assertEqual(high["로그 신호"][jev.LOG_LABELS["slow_query"]], "없음")
        self.assertEqual(high["이상 시작 직전 배포·설정 변경"], "있음")

        low = jev.build_state({"metrics": {"error_rate": 0.049, "p95_seconds": 0.99}, "recent_change": None})
        self.assertEqual(low["5xx 에러율"], "정상 (4.9%, 기준 5% 미만)")
        self.assertEqual(low["p95 응답시간"], "정상 (0.99초, 기준 1초 미만)")
        self.assertEqual(set(low["로그 신호"].values()), {"없음"})
        self.assertEqual(low["이상 시작 직전 배포·설정 변경"], "알 수 없음")

        empty = jev.build_state({})
        self.assertEqual(empty["5xx 에러율"], "알 수 없음")
        self.assertEqual(empty["p95 응답시간"], "알 수 없음")


class ClassifyFaultTests(unittest.IsolatedAsyncioTestCase):
    SIGNALS = {"metrics": {"error_rate": 0.46}, "logs": {"connection_pool_exhausted": 50}, "recent_change": True}

    async def test_answer_failure_and_missing_key(self):  # T14-2
        answer = SimpleNamespace(choice="RB-STD-01", confidence=0.93,
                                 probabilities={"RB-STD-01": 0.93, "RB-STD-02": 0.07})
        client = SimpleNamespace(system_one=AsyncMock(
            return_value=SimpleNamespace(answers={"fault": answer})))
        result = await jev.classify_fault(self.SIGNALS, client=client)
        self.assertEqual(result, {"choice": "RB-STD-01", "confidence": 0.93,
                                  "probabilities": {"RB-STD-01": 0.93, "RB-STD-02": 0.07}})
        question = client.system_one.await_args.kwargs["questions"]["fault"]
        self.assertEqual(list(question["criteria"]),
                         ["RB-STD-01", "RB-STD-02", "RB-STD-03", "RB-STD-04", "RB-STD-05", "RB-STD-07"])

        failing = SimpleNamespace(system_one=AsyncMock(side_effect=RuntimeError("502")))
        self.assertIsNone(await jev.classify_fault(self.SIGNALS, client=failing))

        async def slow(**_):
            await asyncio.sleep(1)
        self.assertIsNone(await jev.classify_fault(self.SIGNALS, client=SimpleNamespace(system_one=slow),
                                                   timeout=0.01))

        with patch.dict(os.environ, {}, clear=True), patch.object(jev, "_new_client") as new_client:
            self.assertIsNone(await jev.classify_fault(self.SIGNALS))
        new_client.assert_not_called()


class ProposeWithJevTests(unittest.IsolatedAsyncioTestCase):
    STATE = {"task_id": "t", "incident_id": 7, "turns": 1, "tool_counts": {}, "findings": "DB 지연",
             "signals": {"metrics": {"error_rate": 0.01}, "logs": {"slow_query": 31}}, "runbook": [], "context": {}}

    async def propose(self, fault):  # GPT는 도구 없이 답한다. GPT 입력과 STEP 이벤트만 본다
        seen = []

        class StubModel:
            async def ainvoke(self, messages):
                seen.append(messages[1].content)
                return AIMessage(content="조치 없음")

        inventory = ToolMessage(content=json.dumps([]), status="success", tool_call_id="i")
        runtime = SimpleNamespace(context=SimpleNamespace(
            toolbox=SimpleNamespace(call=AsyncMock(return_value=inventory), for_node=lambda node: []),
            check_time=lambda: None))
        with patch("agent.graph.harness.chat_model", return_value=StubModel()), \
                patch("agent.graph.jev.classify_fault", new=AsyncMock(return_value=fault)), \
                patch("agent.graph.backend.emit", new=AsyncMock()) as emit:
            await graph.propose_action(self.STATE, runtime)
        steps = [c.args[2] for c in emit.await_args_list if c.args[1] == "STEP"]
        return seen[0], steps

    async def test_confident_verdict_goes_to_gpt_otherwise_only_recorded(self):  # T14-3
        prompt, steps = await self.propose({"choice": "RB-STD-05", "confidence": 0.99, "probabilities": {}})
        self.assertIn("장애 형태(Jev 판정, 확신도 99%): RB-STD-05", prompt)
        self.assertEqual(steps, [{"node": "propose_action", "fault": "RB-STD-05", "confidence": 0.99, "applied": True}])

        for low in ({"choice": "RB-STD-02", "confidence": 0.6}, {"choice": "RB-STD-07", "confidence": 0.99}):
            prompt, steps = await self.propose({**low, "probabilities": {}})
            self.assertNotIn("Jev 판정", prompt)
            self.assertFalse(steps[0]["applied"])

        prompt, steps = await self.propose(None)
        self.assertNotIn("Jev 판정", prompt)
        self.assertEqual(steps, [])

    async def test_log_signal_with_digits_is_counted(self):  # T14-4
        line = ("order rejected: 503 worker pool saturated (active=200/200, queued=1000/1000), "
                "oldest worker blocked 251s")
        rows = [{"line": line, "labels": {"level": "ERROR"}}] * 3
        box = SimpleNamespace(call=AsyncMock(return_value=ToolMessage(
            content=json.dumps(rows), status="success", tool_call_id="l")))
        _, logged = await signals.recent_log_patterns(box, {}, None, None, OBS, "order-api")
        self.assertEqual(logged["signals"]["worker_pool_saturated"], 3)
        self.assertEqual(logged["signals"]["request_queue_backlog"], 0)


if __name__ == "__main__":
    unittest.main()

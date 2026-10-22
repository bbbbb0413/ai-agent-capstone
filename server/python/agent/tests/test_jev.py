"""Jev 호출 없이 장애 형태 판정의 입력 변환과 실패 처리를 검증한다."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent import jev


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


if __name__ == "__main__":
    unittest.main()

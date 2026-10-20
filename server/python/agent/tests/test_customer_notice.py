"""고객 공지 문안 검사 (prompts/notice.md). 실제로 나갔던 문제 문안으로 확인한다."""

import unittest
from unittest.mock import AsyncMock, patch

from agent import customer_notice

STATE = {"task_id": "t", "recovered": False,
         "context": {"incident": {"service": "order-api", "startedAt": "2026-10-20T04:36:36Z"}}}
GOOD = "order-api 서비스에서 오늘 13:36부터 주문 처리 지연이 발생해 복구 작업을 진행하고 있습니다. 30분 안에 다시 안내드리겠습니다."
# a-surge-max에서 실제로 만들어진 문안의 일부
LEAKED = ("참고: 이번 에스컬레이션 요청은 형식 오류로 실패했습니다. 승인하에, restart_pods 또는 scale_deployment 방식으로 "
          "재시도할 예정입니다. अभी 회복되지 않았으므로 다음 안내 시각은 구체적으로 안내드리겠습니다.")
TEMPLATE = "안내 용도: 고객사 담당자 읽기용 간결 공지\n\n1) 현재 예약 플랫폼에서 에러율이 높습니다.\n정상화되었을 경우 안내: 정상화되었다고 공지"


class CustomerNoticeTests(unittest.IsolatedAsyncioTestCase):
    def test_clean_notice_has_no_problems(self):
        self.assertEqual(customer_notice.problems(GOOD), [])

    def test_leaked_internals_and_foreign_word_are_found(self):
        found = " ".join(customer_notice.problems(LEAKED))
        for expected in ("형식 오류", "에스컬레이션", "재시도", "अ"):
            self.assertIn(expected, found)

    def test_copied_instructions_and_lists_are_found(self):
        found = " ".join(customer_notice.problems(TEMPLATE))
        self.assertIn("안내 용도", found)
        self.assertIn("정상화되었을 경우", found)
        self.assertIn("목록", found)

    def test_unrecovered_notice_needs_a_concrete_next_notice_time(self):
        vague = "order-api 서비스에서 주문 처리 지연이 발생해 복구 중입니다. 다음 안내는 확정되는 즉시 드리겠습니다."
        self.assertIn("다음 안내 시각", " ".join(customer_notice.problems(vague, recovered=False)))
        self.assertEqual(customer_notice.problems(GOOD, recovered=False), [])
        self.assertEqual(customer_notice.problems("15시까지 다시 안내드리겠습니다. 복구 중입니다.", recovered=False), [])

    def test_approval_process_and_hostnames_are_internal(self):
        found = " ".join(customer_notice.problems("고객 승인 대기 중이며 pg.example.com 호출이 지연됩니다."))
        self.assertIn("승인", found)
        self.assertIn("pg.example.com", found)

    def test_abbreviation_is_matched_as_a_word(self):
        self.assertIn("내부 용어: PR", customer_notice.problems("PR을 병합했습니다."))
        self.assertEqual(customer_notice.problems("PRODUCT 서비스가 정상화되었습니다."), [])

    async def test_good_draft_is_kept_without_model_call(self):
        with patch.object(customer_notice.harness, "chat_model") as model:
            self.assertEqual(await customer_notice.finalize(GOOD, STATE), GOOD)
        model.assert_not_called()

    async def test_bad_draft_is_rewritten_once(self):
        model = AsyncMock()
        model.ainvoke.return_value.content = GOOD
        with patch.object(customer_notice.harness, "chat_model", return_value=model), \
                patch.object(customer_notice.harness, "prompt", return_value=""):
            self.assertEqual(await customer_notice.finalize(LEAKED, STATE), GOOD)

    async def test_bad_rewrite_falls_back_to_state_based_notice(self):
        model = AsyncMock()
        model.ainvoke.return_value.content = LEAKED
        with patch.object(customer_notice.harness, "chat_model", return_value=model), \
                patch.object(customer_notice.harness, "prompt", return_value=""):
            text = await customer_notice.finalize(LEAKED, STATE)
        self.assertEqual(customer_notice.problems(text), [])
        self.assertIn("order-api", text)
        self.assertIn("13:36", text)       # 04:36 UTC → KST
        self.assertIn("30분 안에", text)   # 회복 전이면 다음 안내 시각

    def test_fallback_for_recovered_incident(self):
        text = customer_notice.fallback({**STATE, "recovered": True})
        self.assertIn("정상화되었습니다", text)
        self.assertEqual(customer_notice.problems(text), [])


if __name__ == "__main__":
    unittest.main()

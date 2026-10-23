"""실제 조치·LLM 호출 없이 MCP 오류, 보고서 근거, 승인 재개를 검증한다."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import CallToolResult, TextContent, Tool

from agent import action_guards, graph, signals, summary
from agent.runner import Runner
from agent.toolbox import ToolBox, parse_json


class ToolOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def call_mcp(self, *, is_error, text):
        session = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(
            isError=is_error, content=[TextContent(type="text", text=text)])))
        spec = Tool(name="send_customer_notice", inputSchema={
            "type": "object", "properties": {"incident_id": {"type": "integer"}},
            "required": ["incident_id"],
        })
        tool = convert_mcp_tool_to_langchain_tool(session, spec, server_name="business", tool_name_prefix=True)
        with patch("agent.toolbox.backend.emit", new=AsyncMock()) as emit:
            result = await ToolBox([tool], "tenant-a", "test").call(
                "notice", tool.name, {"incident_id": 1}, {}, call_id="call-1")
        return result, emit

    async def test_mcp_error_is_preserved_in_message_and_event(self):
        result, emit = await self.call_mcp(is_error=True, text="403: permission denied")
        self.assertEqual(result.status, "error")
        self.assertIn("403", result.content)
        end = emit.await_args_list[-1].args
        self.assertEqual(end[1], "TOOL_END")
        self.assertEqual(end[2]["status"], "error")
        self.assertEqual(end[2]["callId"], "call-1")

    async def test_success_keeps_parseable_json(self):
        result, emit = await self.call_mcp(is_error=False, text='{"sent": true}')
        self.assertEqual(result.status, "success")
        self.assertEqual(parse_json(result), {"sent": True})
        self.assertEqual(emit.await_args_list[-1].args[2]["status"], "success")

    async def test_transport_failure_emits_error_then_propagates(self):
        tool = SimpleNamespace(name="business_send_customer_notice", ainvoke=AsyncMock(side_effect=OSError("offline")))
        with patch("agent.toolbox.backend.emit", new=AsyncMock()) as emit:
            with self.assertRaises(OSError):
                await ToolBox([tool], "tenant-a", "test").call("notice", tool.name, {}, {})
        self.assertEqual(emit.await_args_list[-1].args[2]["status"], "error")

    async def test_failed_execution_never_routes_to_recovery_verification(self):
        result = ToolMessage(content="403: denied", status="error", tool_call_id="test")
        box = SimpleNamespace(call=AsyncMock(return_value=result))
        state = {"proposed_action": {"tool": "business_execute_runbook_action", "args": {}},
                 "tool_counts": {}, "evidence": []}
        update = await graph.execute(state, SimpleNamespace(context=SimpleNamespace(toolbox=box)))
        self.assertEqual(update["action_result"]["status"], "FAILED")
        self.assertEqual(graph.route_execute({**state, **update}), "report")

    async def test_execution_and_pr_creation_have_distinct_routes(self):
        state = {"proposed_action": {"tool": "business_execute_runbook_action"}}
        self.assertEqual(graph.route_execute({**state, "action_result": {"status": "EXECUTED"}}), "verify")
        self.assertEqual(graph.route_execute({**state, "action_result": {"status": "PR_CREATED"},
                                            "change": {"prNumber": 1}}), "await_change")

    async def test_escalation_chosen_instead_of_action_is_queued_and_reported(self):
        queued = {"escalationId": 3, "status": "QUEUED", "category": "ACTION_BLOCKED",
                  "recipients": [{"kind": "CUSTOMER", "name": "B사 운영 승인 담당", "role": "B사"}]}
        result = ToolMessage(content=json.dumps(queued), status="success", tool_call_id="test")
        box = SimpleNamespace(call=AsyncMock(return_value=result))
        state = {"proposed_action": {"tool": "business_escalate_incident",
                                     "args": {"category": "ACTION_BLOCKED", "reason": "확장 계약상 불가"}},
                 "tool_counts": {}, "evidence": []}
        update = await graph.execute(state, SimpleNamespace(context=SimpleNamespace(toolbox=box)))
        self.assertEqual(update["escalation_result"]["status"], "QUEUED")
        self.assertEqual(graph.route_execute({**state, **update}), "report")  # 공지는 보고서 뒤에 따로 승인한다

    def test_log_window_starts_shortly_before_the_incident(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
        started = {"startedAt": "2026-10-20T08:58:00Z"}
        self.assertEqual(signals.log_window_start(now, started), now - timedelta(minutes=4))
        old = {"startedAt": "2026-10-20T07:00:00Z"}   # 오래된 인시던트는 최근 10분
        self.assertEqual(signals.log_window_start(now, old), now - timedelta(minutes=10))
        self.assertEqual(signals.log_window_start(now, None), now - timedelta(minutes=10))

    def test_only_an_autoscaler_still_scaling_up_allows_no_action(self):
        def ctx(desired, maximum):
            return {"services": [{"name": "order-api", "autoscaling": {
                "minReplicas": 2, "maxReplicas": maximum, "desiredReplicas": desired,
                "atMax": desired >= maximum}}]}
        self.assertTrue(graph._autoscaler_scaling(ctx(3, 4), "order-api"))     # 늘리는 중
        self.assertFalse(graph._autoscaler_scaling(ctx(3, 3), "order-api"))    # 최대치: 조치 또는 에스컬레이션
        self.assertFalse(graph._autoscaler_scaling(ctx(2, 3), "order-api"))    # 부하와 무관한 장애
        self.assertFalse(graph._autoscaler_scaling({"services": [{"name": "reservation-api"}]}, "reservation-api"))

    def test_action_that_does_not_fit_the_cited_clause_is_sent_back(self):
        from langchain_core.messages import AIMessage
        def ai(action, reason):
            return AIMessage(content="", tool_calls=[{"name": "business_execute_runbook_action", "id": "c1",
                                                      "args": {"action": action, "reason": reason}}])
        self.assertIn("ACTION_BLOCKED", action_guards.clause_mismatch(ai("restart_pods", "RB-STD-03 트래픽 급증, 큐를 비운다")))
        self.assertIsNone(action_guards.clause_mismatch(ai("adjust_autoscaler", "RB-STD-03에 따라 최대치를 올린다")))
        self.assertIsNone(action_guards.clause_mismatch(ai("restart_pods", "RB-STD-02 작업 스레드 고갈")))
        self.assertIsNone(action_guards.clause_mismatch(ai("restart_pods", "조항 인용 없음")))
        # RB-STD-01의 재시작은 직전 변경이 없을 때만. 직전 변경이 있으면 롤백, 불가하면 에스컬레이션
        self.assertIsNone(action_guards.clause_mismatch(ai("restart_pods", "RB-STD-01 풀 고갈, 직전 변경 없음")))
        self.assertIn("ACTION_BLOCKED", action_guards.clause_mismatch(ai("restart_pods", "RB-STD-01 풀 고갈"), recent_change=True))
        self.assertIsNone(action_guards.clause_mismatch(ai("rollback_deployment", "RB-STD-01 풀 고갈"), recent_change=True))

    def test_recovery_needs_error_metrics_and_both_thresholds(self):
        self.assertTrue(signals.within_thresholds({"error_rate": 0.0, "p95_seconds": 0.6}))
        self.assertTrue(signals.within_thresholds({"error_rate": 0.0, "p95_seconds": None}))
        self.assertFalse(signals.within_thresholds({"error_rate": None, "p95_seconds": 0.1}))   # 요청 메트릭 미수집
        self.assertFalse(signals.within_thresholds({"error_rate": 0.0, "p95_seconds": 3.0}))

    def test_action_outside_supported_actions_is_sent_back(self):
        from langchain_core.messages import AIMessage
        def ai(action):
            return AIMessage(content="", tool_calls=[{"name": "business_execute_runbook_action", "id": "c1",
                                                      "args": {"action": action, "service": "reservation-api"}}])
        context = {"services": [{"name": "reservation-api", "supportedActions": ["restart_pods"]}]}
        problem = action_guards.unsupported_action(ai("rollback_deployment"), context)
        self.assertIn("ACTION_BLOCKED", problem)
        self.assertIn("rollback_deployment", problem)
        self.assertIsNone(action_guards.unsupported_action(ai("restart_pods"), context))
        self.assertIsNone(action_guards.unsupported_action(ai("restart_pods"), {}))   # 서비스 목록이 없으면 판단하지 않는다

    def test_blocked_escalation_must_name_the_action_of_the_signalled_fault(self):
        from langchain_core.messages import AIMessage
        def esc(action):
            return AIMessage(content="", tool_calls=[{"name": "business_escalate_incident", "id": "c1", "args": {
                "category": "ACTION_BLOCKED", "required_action": action, "blocked_by": "CONTRACT", "reason": "x"}}])
        pool = {"metrics": {"error_rate": 0.51}, "logs": {"connection_pool_exhausted": 50}}
        self.assertIn("RB-STD-01", action_guards.blocked_action_mismatch(esc("scale_deployment"), pool, True))
        self.assertIsNone(action_guards.blocked_action_mismatch(esc("rollback_deployment"), pool, True))
        surge = {"metrics": {"error_rate": 0.0}, "logs": {"request_queue_backlog": 120}}
        self.assertIsNone(action_guards.blocked_action_mismatch(esc("scale_deployment"), surge, False))
        self.assertIn("RB-STD-03", action_guards.blocked_action_mismatch(esc("rollback_deployment"), surge, False))
        self.assertIsNone(action_guards.blocked_action_mismatch(esc("scale_deployment"), {"metrics": {}, "logs": {}}, False))

    def test_signal_clause_is_decided_only_when_one_fault_shape_fits(self):
        both = {"metrics": {"error_rate": 0.4}, "logs": {"connection_pool_exhausted": 5, "worker_pool_saturated": 5}}
        self.assertIsNone(action_guards.signal_clause(both))   # 풀 고갈과 스레드 고갈이 함께면 코드가 정하지 않는다
        self.assertEqual(action_guards.signal_clause({"metrics": {"error_rate": 0.4},
                                               "logs": {"worker_pool_saturated": 5}}), "RB-STD-02")
        # 대기열 경고가 있어도 외부 API 지연이나 느린 쿼리가 함께면 트래픽 급증(RB-STD-03)으로 보지 않는다
        for cause in ("external_api_timeout", "slow_query"):
            self.assertIsNone(action_guards.signal_clause({"metrics": {"error_rate": 0.0},
                                                    "logs": {"request_queue_backlog": 50, cause: 3}}))

    def test_blocked_scaling_must_fit_whether_the_service_has_an_autoscaler(self):
        from langchain_core.messages import AIMessage
        def esc(service, action):
            return AIMessage(content="", tool_calls=[{"name": "business_escalate_incident", "id": "c1", "args": {
                "category": "ACTION_BLOCKED", "service": service, "required_action": action, "reason": "x"}}])
        context = {"services": [{"name": "reservation-api", "supportedActions": ["restart_pods"]},
                                {"name": "order-api", "autoscaling": {"maxReplicas": 4}}]}
        problem = action_guards.blocked_scaling_unfit(esc("reservation-api", "adjust_autoscaler"), context)
        self.assertIn("scale_deployment", problem)   # b-surge: HPA가 없는 서비스
        self.assertIn("CONTRACT", problem)
        self.assertIsNone(action_guards.blocked_scaling_unfit(esc("reservation-api", "scale_deployment"), context))
        self.assertIn("adjust_autoscaler", action_guards.blocked_scaling_unfit(esc("order-api", "scale_deployment"), context))
        self.assertIsNone(action_guards.blocked_scaling_unfit(esc("order-api", "adjust_autoscaler"), context))
        self.assertIsNone(action_guards.blocked_scaling_unfit(esc("unknown", "adjust_autoscaler"), context))
        self.assertIsNone(action_guards.blocked_scaling_unfit(esc("reservation-api", "rollback_deployment"), context))

    def test_propose_send_back_runs_every_guard(self):
        from langchain_core.messages import AIMessage
        def call(name, args, content=""):
            return AIMessage(content=content, tool_calls=[{"name": name, "id": "c1", "args": args}])
        context = {"services": [{"name": "reservation-api", "supportedActions": ["restart_pods"]}]}
        pool = {"metrics": {"error_rate": 0.5}, "logs": {"connection_pool_exhausted": 9}}
        cases = {
            "clause": call("business_execute_runbook_action", {"action": "restart_pods", "service": "reservation-api",
                                                               "reason": "RB-STD-03"}),
            "supported": call("business_execute_runbook_action", {"action": "rollback_deployment",
                                                                  "service": "reservation-api"}),
            "blocked": call("business_escalate_incident", {"category": "ACTION_BLOCKED", "reason": "x",
                                                           "required_action": "scale_deployment"}),
        }
        for name, ai in cases.items():
            with self.subTest(name):
                self.assertIsNotNone(action_guards.first_problem(ai, context, pool, True))
        # 트래픽 급증 신호면 adjust_autoscaler도 그 장애 형태의 조치다. HPA가 없다는 구조 검사만 잡는다
        surge = {"metrics": {"error_rate": 0.0}, "logs": {"request_queue_backlog": 40}}
        unfit = call("business_escalate_incident", {"category": "ACTION_BLOCKED", "reason": "x",
                                                    "service": "reservation-api", "required_action": "adjust_autoscaler"})
        self.assertIsNotNone(action_guards.first_problem(unfit, context, surge, False))
        fine = call("business_execute_runbook_action", {"action": "restart_pods", "service": "reservation-api",
                                                        "reason": "RB-STD-02"})
        self.assertIsNone(action_guards.first_problem(fine, context, pool, False))

    def test_blocked_escalation_cannot_name_restart_after_a_recent_change(self):
        from langchain_core.messages import AIMessage
        esc = AIMessage(content="", tool_calls=[{"name": "business_escalate_incident", "id": "c1", "args": {
            "category": "ACTION_BLOCKED", "required_action": "restart_pods", "blocked_by": "CONTRACT", "reason": "x"}}])
        pool = {"metrics": {"error_rate": 0.51}, "logs": {"connection_pool_exhausted": 50}}
        self.assertIn("RB-STD-01", action_guards.blocked_action_mismatch(esc, pool, True))
        self.assertIsNone(action_guards.blocked_action_mismatch(esc, pool, False))

    async def test_confirm_sample_over_threshold_overrides_model_recovered(self):
        from langchain_core.messages import AIMessage
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="RECOVERED")))
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=None, settle=AsyncMock(), check_time=lambda: None))
        state = {"task_id": "t", "tool_counts": {}, "turns": 0, "evidence": [], "findings": "급증",
                 "proposed_action": {"tool": "business_execute_runbook_action", "args": {}}}
        # 첫 측정은 기준 안, 20초 뒤 다시 잰 p95가 기준(1초)을 넘는다
        with patch("agent.signals.metric", new=AsyncMock(side_effect=[0.0, 0.5, 0.0, 3.0])), \
                patch("agent.graph.harness.chat_model", return_value=model), \
                patch("agent.graph.backend.emit", new=AsyncMock()):
            result = await graph.verify(state, runtime)
        self.assertFalse(result["recovered"])
        self.assertTrue(result["verification"].startswith("NOT_RECOVERED"))

    def test_recent_change_is_a_change_shortly_before_the_incident(self):
        b = {"revisions": [{"createdAt": "2026-10-20T04:13:12Z"}, {"createdAt": "2026-10-20T02:38:12Z"}]}
        self.assertTrue(signals.recent_change(b, "2026-10-20T04:14:34Z"))
        self.assertFalse(signals.recent_change(b, "2026-10-20T05:00:00Z"))
        # 예전 설정으로 다시 배포하면 ReplicaSet 생성 시각은 옛 값이고, 실제 배포 시각은 deployedAt에 있다
        reused = {"revisions": [{"createdAt": "2026-10-20T05:00:00Z", "deployedAt": "2026-10-20T06:28:25Z"}]}
        self.assertTrue(signals.recent_change(reused, "2026-10-20T06:29:20Z"))
        a = {"commits": [{"date": "2026-10-20T02:50:01Z"}]}
        self.assertTrue(signals.recent_change(a, "2026-10-20T03:00:00+00:00"))
        self.assertIsNone(signals.recent_change(None, "2026-10-20T03:00:00Z"))

    def test_flow_escalation_category_follows_the_reason(self):
        base = {"incident_id": 1, "proposed_action": {"args": {"action": "restart_pods", "service": "order-api"}}}
        self.assertEqual(graph._escalation_args({**base, "attempts": 0})["category"], "P1")
        args = graph._escalation_args({**base, "attempts": 1})
        self.assertEqual((args["category"], args["service"]), ("NOT_RECOVERED", "order-api"))
        timeout = graph._escalation_args({**base, "attempts": 0, "change": {"result": "TIMEOUT", "prUrl": "u"}})
        self.assertEqual(timeout["category"], "CHANGE_TIMEOUT")

    def test_approval_uses_service_inventory_for_rolling_restart_impact(self):
        state = {"task_id": "test", "approvals": [], "context": {
            "tenant": {"changeMode": "GITOPS"},
            "services": [{"name": "order-api", "runtime": "KUBERNETES", "replicas": 2,
                          "readyReplicas": 2, "safeMaxReplicas": 4, "zeroDowntimeRestart": True}],
        }}
        request = graph._approval_request("business_execute_runbook_action",
                                          {"action": "restart_pods", "service": "order-api"}, "hang", state)
        self.assertEqual(request["changeMode"], "GITOPS")
        self.assertEqual(request["impact"]["restartMode"], "ROLLING")
        self.assertEqual(request["impact"]["replicas"], 2)
        self.assertTrue(request["impact"]["zeroDowntime"])

    def test_scale_approval_includes_current_target_and_safe_max(self):
        state = {"task_id": "test", "approvals": [], "context": {"services": [
            {"name": "order-api", "replicas": 2, "readyReplicas": 2, "safeMaxReplicas": 4},
        ]}}
        request = graph._approval_request("business_execute_runbook_action",
                                          {"action": "scale_deployment", "service": "order-api", "replicas": 4},
                                          "capacity", state)
        self.assertEqual(request["impact"], {
            "replicas": 2, "readyReplicas": 2, "safeMaxReplicas": 4, "targetReplicas": 4,
        })


class ReportTests(unittest.IsolatedAsyncioTestCase):
    def test_unlinked_sla_question_routes_directly_to_document_search(self):
        state = {"type": "QUESTION", "incident_id": None, "question": "A사 P1/P2 SLA 응답 기한은?",
                 "context": {}}
        self.assertEqual(graph.route_triage(state), "retrieve_runbook")
        state["question"] = "A사 서비스 상태가 어때?"
        self.assertEqual(graph.route_triage(state), "diagnose")

    async def test_unlinked_question_does_not_propose_an_invalid_execution(self):
        with patch("agent.graph.harness.chat_model") as model:
            update = await graph.propose_action({"type": "QUESTION", "incident_id": None}, None)
        self.assertIsNone(update["proposed_action"])
        model.assert_not_called()

    async def test_report_and_summary_receive_actual_outcome(self):
        state = {"question": "why?", "task_id": "test", "evidence": [], "context": {},
                 "proposed_action": {"tool": "business_execute_runbook_action", "args": {}},
                 "action_result": {"status": "PR_CREATED", "prUrl": "https://example.invalid/pr/1"},
                 "change": {"prNumber": 1, "result": "SYNCED"},
                 "approvals": [{"tool": "business_execute_runbook_action", "decision": "APPROVE"}]}
        payloads = []

        class StubModel:
            async def astream(self, messages):
                payloads.append(json.loads(messages[1].content))
                yield SimpleNamespace(content="## 요약\nPR 반영 완료")

        with patch("agent.graph.harness.chat_model", return_value=StubModel()), \
                patch("agent.graph.backend.emit", new=AsyncMock()), \
                patch("agent.graph.summary.classify", new=AsyncMock(return_value={"error_type": "기타"})):
            update = await graph.report(state, None)
        self.assertEqual(payloads[0]["action_result"], state["action_result"])
        self.assertEqual(payloads[0]["change"], state["change"])
        built = summary.build({**state, **update})
        self.assertEqual(built["action"]["result"]["status"], "PR_CREATED")
        self.assertEqual(built["change"]["result"], "SYNCED")

    async def test_unlinked_information_question_skips_incident_classification(self):
        state = {"type": "QUESTION", "incident_id": None, "question": "SLA는?", "task_id": "test",
                 "evidence": [], "runbook": [{"uri": "docs://tenant-a/sla"}], "context": {},
                 "approvals": []}

        class StubModel:
            async def astream(self, messages):
                yield SimpleNamespace(content="## 답변\nP1은 15분")

        with patch("agent.graph.harness.chat_model", return_value=StubModel()), \
                patch("agent.graph.backend.emit", new=AsyncMock()), \
                patch("agent.graph.summary.classify", new=AsyncMock()) as classify:
            update = await graph.report(state, None)
        self.assertIsNone(update["classification"])
        classify.assert_not_awaited()

    async def test_report_tokens_are_sent_in_batches_with_the_full_text(self):
        state = {"type": "QUESTION", "incident_id": None, "question": "SLA는?", "task_id": "test",
                 "evidence": [], "runbook": [], "context": {}, "approvals": []}
        parts = [f"조각{i} " for i in range(50)]

        class StubModel:
            async def astream(self, messages):
                for part in parts:
                    yield SimpleNamespace(content=part)

        with patch("agent.graph.harness.chat_model", return_value=StubModel()), \
                patch("agent.graph.backend.emit", new=AsyncMock()) as emit:
            await graph.report(state, None)
        tokens = [c.args[2]["text"] for c in emit.await_args_list if c.args[1] == "TOKEN"]
        self.assertLess(len(tokens), 5)   # 50조각을 한 번에 몇 번으로 묶었다
        self.assertEqual("".join(tokens), "".join(parts))

    async def test_notice_failure_is_not_reported_as_sent(self):
        state = {"task_id": "test", "incident_id": 1, "notice_draft": "test", "tool_counts": {}, "approvals": []}
        result = ToolMessage(content="403: denied", status="error", tool_call_id="test")
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=SimpleNamespace(call=AsyncMock(return_value=result))))
        with patch("agent.graph.interrupt", return_value={"decision": "APPROVE"}):
            update = await graph.notice_approval(state, runtime)
        self.assertEqual(update["notice_result"]["status"], "FAILED")


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        snapshot = SimpleNamespace(interrupts=[SimpleNamespace(value={"approvalId": "current"})],
                                   values={"tenant_id": "tenant-a", "trace_id": "trace"})
        fake_graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
        return Runner(fake_graph, None), snapshot

    async def test_stale_approval_never_resumes_new_interrupt(self):
        runner, _ = self.runner()
        runner._drive = AsyncMock()
        self.assertEqual(await runner.resume("test", {"approvalId": "old"}), "DUPLICATE")
        runner._drive.assert_not_awaited()

    async def test_concurrent_duplicate_resume_only_drives_once(self):
        runner, snapshot = self.runner()

        async def drive(*args, **kwargs):
            await asyncio.sleep(0)
            snapshot.interrupts = []
            return "DONE"

        runner._drive = AsyncMock(side_effect=drive)
        outcomes = await asyncio.gather(*(runner.resume("test", {"approvalId": "current"}) for _ in range(2)))
        self.assertEqual(sorted(outcomes), ["DONE", "DUPLICATE"])
        self.assertEqual(runner._drive.await_count, 1)

    async def test_resume_waits_for_a_dead_pods_lease_instead_of_dropping_the_decision(self):
        runner, _ = self.runner()
        runner._drive = AsyncMock(side_effect=["RUNNING_ELSEWHERE", "RUNNING_ELSEWHERE", "DONE"])
        with patch("agent.runner.RESUME_LEASE_POLL_SECONDS", 0):
            self.assertEqual(await runner.resume("test", {"approvalId": "current"}), "DONE")
        self.assertEqual(runner._drive.await_count, 3)

    async def test_resume_that_never_gets_the_lease_fails_the_task_visibly(self):
        runner, _ = self.runner()
        runner._drive = AsyncMock(return_value="RUNNING_ELSEWHERE")
        with patch("agent.runner.RESUME_LEASE_POLL_SECONDS", 0), patch("agent.runner.LEASE_SECONDS", 0), \
                patch("agent.runner.HEARTBEAT_SECONDS", 0), \
                patch("agent.runner.backend.result", new=AsyncMock()) as result:
            self.assertEqual(await runner.resume("test", {"approvalId": "current"}), "FAILED")
        self.assertEqual(result.await_args.kwargs["status"], "FAILED")

    async def test_redelivered_unfinished_task_continues_from_checkpoint(self):
        snapshot = SimpleNamespace(values={"tenant_id": "tenant-a", "trace_id": "x"}, next=("diagnose",), interrupts=[])
        runner = Runner(SimpleNamespace(aget_state=AsyncMock(return_value=snapshot)), None)
        runner._drive = AsyncMock(return_value="DONE")
        message = {"taskId": "t1", "tenantId": "tenant-a", "traceId": "x"}
        self.assertEqual(await runner.run(message), "DONE")
        self.assertIsNone(runner._drive.await_args.args[1])   # 새 입력이 아니라 checkpoint에서 이어 간다

    async def test_finished_or_waiting_task_is_duplicate(self):
        for snapshot in (SimpleNamespace(values={"x": 1}, next=(), interrupts=[]),
                         SimpleNamespace(values={"x": 1}, next=("approval",), interrupts=["waiting"])):
            runner = Runner(SimpleNamespace(aget_state=AsyncMock(return_value=snapshot)), None)
            runner._drive = AsyncMock()
            self.assertEqual(await runner.run({"taskId": "t1", "tenantId": "a", "traceId": "x"}), "DUPLICATE")
            runner._drive.assert_not_awaited()

    async def test_lease_held_elsewhere_skips_execution(self):
        runner = Runner(None, None)
        runner._claim = AsyncMock(return_value=False)
        runner._drive_claimed = AsyncMock()
        self.assertEqual(await runner._drive("t1", None, {}, {}, False), "RUNNING_ELSEWHERE")
        runner._drive_claimed.assert_not_awaited()

    async def test_task_not_active_in_backend_is_not_run(self):
        """끝났거나 사람을 기다리는 작업(DLT에서 다시 꺼낸 메시지 등)은 토큰을 받지 못하고 실행하지 않는다."""
        from agent.backend import TaskFinished
        graph_ = SimpleNamespace(ainvoke=AsyncMock())
        runner = Runner(graph_, None)
        runner._elapsed = AsyncMock(return_value=0)
        runner._settle_lease = AsyncMock()
        state = {"tenant_id": "tenant-a", "trace_id": "x", "type": "QUESTION"}
        with patch("agent.runner.backend.task_token", AsyncMock(side_effect=TaskFinished("409"))), \
                patch("agent.runner.load_tools", AsyncMock()) as tools, \
                patch("agent.runner.backend.result", AsyncMock()) as result:
            self.assertEqual(await runner._drive_claimed("t1", None, state, {}, True), "NOT_ACTIVE")
        tools.assert_not_awaited()
        graph_.ainvoke.assert_not_awaited()
        result.assert_not_awaited()   # 이미 끝난 작업의 결과를 덮어쓰지 않는다

    async def test_each_run_passes_a_fresh_task_token_to_the_tools(self):
        graph_ = SimpleNamespace(ainvoke=AsyncMock(return_value={"report": "ok"}))
        runner = Runner(graph_, None)
        runner._elapsed = AsyncMock(return_value=0)
        runner._settle_lease = AsyncMock()
        state = {"tenant_id": "tenant-a", "trace_id": "x", "type": "QUESTION"}
        with patch("agent.runner.backend.task_token", AsyncMock(return_value="tok-1")) as token, \
                patch("agent.runner.load_tools", AsyncMock(return_value=[])) as tools, \
                patch("agent.runner.backend.result", AsyncMock()):
            await runner._drive_claimed("t1", None, state, {}, False)
        token.assert_awaited_once_with("t1")
        self.assertEqual(tools.await_args.args[3], "tok-1")

    async def test_lease_bookkeeping_failure_does_not_hide_the_result(self):
        runner = Runner(None, None)
        runner._add_elapsed = AsyncMock(side_effect=OSError("db down"))
        await runner._settle_lease("t1", 1.0, True)   # 예외가 나가지 않는다

    async def test_same_process_duplicate_does_not_run_twice(self):
        runner = Runner(None, None)
        runner._claim = AsyncMock(return_value=True)
        gate = asyncio.Event()

        async def drive(*args):
            await gate.wait()
            return "DONE"

        runner._drive_claimed = AsyncMock(side_effect=drive)
        first = asyncio.create_task(runner._drive("t1", None, {}, {}, False))
        await asyncio.sleep(0)
        self.assertEqual(await runner._drive("t1", None, {}, {}, False), "RUNNING_ELSEWHERE")
        gate.set()
        self.assertEqual(await first, "DONE")
        self.assertEqual(runner._drive_claimed.await_count, 1)

    def test_approval_identity_is_stable_and_advances_with_decisions(self):
        state = {"task_id": "test", "approvals": []}
        make = lambda: graph._approval_request("tool", {}, "test", state)["approvalId"]
        first = make()
        self.assertEqual(first, make())
        state["approvals"].append({"decision": "APPROVE"})
        self.assertNotEqual(first, make())


if __name__ == "__main__":
    unittest.main()

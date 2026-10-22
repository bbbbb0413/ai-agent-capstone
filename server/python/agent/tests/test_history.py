import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage

from agent import graph, signals


STATE = {"tenant_id": "tenant-a", "incident_id": 10, "question": "order-api 커넥션 풀 고갈",
         "context": {"incident": {"service": "order-api"}}}


def result(rows, status="success"):
    return ToolMessage(content=json.dumps(rows), tool_call_id="history", status=status)


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_and_other_tenant_documents_are_excluded(self):
        rows = [{"uri": "docs://tenant-a/postmortem-incident-1", "doc_id": "postmortem-incident-1",
                 "doc_type": "postmortem", "content": "과거 보고서"},
                {"uri": "docs://tenant-b/secret", "doc_id": "secret", "doc_type": "postmortem"},
                {"uri": "docs://tenant-a/postmortem-incident-10", "doc_id": "postmortem-incident-10",
                 "doc_type": "postmortem"}]
        box = SimpleNamespace(call=AsyncMock(return_value=result(rows)))
        self.assertEqual(await graph.retrieve_history(STATE, box, {}, {"connection_pool_exhausted": 4}), rows[:1])
        args = box.call.call_args.args[2]
        self.assertEqual(args["scope"], "tenant")
        self.assertEqual(args["doc_type"], "postmortem")
        self.assertEqual(args["exclude_doc_id"], "postmortem-incident-10")
        self.assertIn("connection_pool_exhausted", args["query"])

    async def test_missing_failed_or_malformed_history_does_not_stop_diagnosis(self):
        for response in (result([]), result("unavailable", "error"), result("truncated")):
            box = SimpleNamespace(call=AsyncMock(return_value=response))
            self.assertEqual(await graph.retrieve_history(STATE, box, {}, {}), [])
        for error in (TimeoutError(), RuntimeError("MCP down")):
            box = SimpleNamespace(call=AsyncMock(side_effect=error))
            self.assertEqual(await graph.retrieve_history(STATE, box, {}, {}), [])

    async def test_history_does_not_displace_runbooks(self):
        box = SimpleNamespace(call=AsyncMock(return_value=result([])))
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=box))
        await graph.retrieve_runbook({**STATE, "tool_counts": {}}, runtime)
        self.assertEqual([c.args[2]["doc_type"] for c in box.call.call_args_list], ["runbook", "runbook", "sla"])

    async def test_first_diagnosis_passes_history_to_model_and_persists_sources(self):
        past = [{"uri": "docs://tenant-a/postmortem-incident-1", "content": "과거 롤백 기록"}]
        logged = {"args": {"startRfc3339": "2026-10-20T00:00:00Z"}, "signals": {}}
        box = SimpleNamespace(call=AsyncMock(return_value=result([])))
        runtime = SimpleNamespace(context=SimpleNamespace(toolbox=box))
        with patch.object(signals, "recent_log_patterns", AsyncMock(return_value=("", logged))), \
                patch.object(signals, "metric", AsyncMock(return_value=0.1)), \
                patch.object(graph, "retrieve_history", AsyncMock(return_value=past)) as retrieve, \
                patch.object(graph, "_tool_loop", AsyncMock(return_value=([result([])], 1, {}, []))) as loop:
            update = await graph.diagnose({**STATE, "tool_counts": {}, "evidence": []}, runtime)
        retrieve.assert_awaited_once()
        self.assertIn("과거 롤백 기록", loop.call_args.args[3][-1].content)
        self.assertEqual(update["past_incidents"], past)


if __name__ == "__main__":
    unittest.main()

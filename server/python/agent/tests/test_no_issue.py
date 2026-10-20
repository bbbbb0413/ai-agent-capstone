"""인식된 장애가 없는 질문의 "이상 없음" 경로를 LLM·MCP 호출 없이 검증한다 (docs/기능별-변경사항.md T7)."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from agent import graph


def question(text: str = "A사 order-api 지금 느린데 왜 그래?", **context) -> dict:
    return {"type": "QUESTION", "incident_id": None, "question": text, "context": context}


def runtime_with(incidents: ToolMessage) -> SimpleNamespace:
    def call(node, name, args, counts):
        if name == "business_list_incidents":
            return incidents
        return ToolMessage(content=json.dumps({"name": "A사 온라인 쇼핑몰"}), status="success", tool_call_id="t")

    return SimpleNamespace(context=SimpleNamespace(toolbox=SimpleNamespace(call=AsyncMock(side_effect=call))))


class NoIssueRouteTests(unittest.TestCase):
    def test_t7_1_no_open_incident_answers_no_issue_otherwise_diagnoses(self):
        self.assertEqual(graph.route_triage(question(open_incidents=[])), "no_issue")
        self.assertEqual(graph.route_triage(question(open_incidents=[{"id": 1, "severity": "P2"}])), "diagnose")

    def test_t7_2_failed_incident_lookup_is_not_treated_as_no_issue(self):
        self.assertEqual(graph.route_triage(question(open_incidents=None)), "diagnose")

    def test_t7_3_document_question_goes_to_documents_before_no_issue(self):
        state = question("A사 SLA 계약서의 P1 응답 기한은?", open_incidents=[])
        self.assertEqual(graph.route_triage(state), "retrieve_runbook")


class NoIssueContextTests(unittest.IsolatedAsyncioTestCase):
    async def load(self, incidents: ToolMessage) -> dict:
        state = {**question(), "tool_counts": {}}
        update = await graph.load_context(state, runtime_with(incidents))
        return update["context"]

    async def test_t7_4_empty_content_means_none_open_and_error_means_unknown(self):
        empty = ToolMessage(content="<tool_output>\n\n</tool_output>", status="success", tool_call_id="t")
        self.assertEqual((await self.load(empty))["open_incidents"], [])

        error = ToolMessage(content="<tool_output>\n500: business-mcp 응답 없음\n</tool_output>",
                            status="error", tool_call_id="t")
        self.assertIsNone((await self.load(error))["open_incidents"])


if __name__ == "__main__":
    unittest.main()

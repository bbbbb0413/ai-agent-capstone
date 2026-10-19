"""MCP 도구 연결과 Harness가 적용된 도구 실행기.

모든 도구 호출은 ToolBox.call을 거친다. 여기서 Node별 허용목록, 같은 도구 횟수 한도, 테넌트 데이터소스 강제,
민감정보 마스킹, 결과 길이 제한, 진행 이벤트 발행을 한 번에 처리한다. 오류는 예외가 아니라 ToolMessage로 모델에 돌려준다.
"""

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent import harness
from agent.backend import backend
from agent.config import settings


async def load_tools(tenant_id: str, task_id: str, trace_id: str) -> list[BaseTool]:
    """세 MCP Server의 도구를 LangChain 도구로 받는다. 고객사·추적 정보는 요청 헤더로 싣는다."""
    headers = {
        "Authorization": f"Bearer {settings.service_token}",
        "X-Tenant-Id": tenant_id,
        "X-Task-Id": task_id,
        "X-Trace-Id": trace_id,
    }
    client = MultiServerMCPClient(
        {
            "business": {"url": settings.business_mcp_url, "transport": "streamable_http", "headers": headers},
            "rag": {"url": settings.rag_mcp_url, "transport": "streamable_http", "headers": headers},
            # 관측 데이터의 고객사 격리는 헤더가 아니라 데이터소스 강제(harness)로 한다
            "grafana": {"url": settings.grafana_mcp_url, "transport": "streamable_http",
                        "headers": {"Authorization": headers["Authorization"]}},
        },
        tool_name_prefix=True,
    )
    return await client.get_tools()


class ToolBox:
    def __init__(self, tools: list[BaseTool], tenant_id: str, task_id: str) -> None:
        self.by_name = {tool.name: tool for tool in tools}
        self.tenant_id = tenant_id
        self.task_id = task_id

    def for_node(self, node: str) -> list[BaseTool]:
        return [tool for name, tool in self.by_name.items() if name in harness.NODE_TOOLS[node]]

    async def call(self, node: str, name: str, args: dict[str, Any], counts: dict[str, int],
                   call_id: str = "direct", incident_id: int | None = None) -> ToolMessage:
        """counts(도구별 호출 횟수)는 호출한 쪽이 State에 저장한다."""
        if name not in harness.NODE_TOOLS[node] or name not in self.by_name:
            return self._error(call_id, name, f"'{name}'은 이 단계({node})에서 쓸 수 없는 도구다")
        if counts.get(name, 0) >= harness.MAX_SAME_TOOL:
            return self._error(call_id, name, f"'{name}'을 이미 {harness.MAX_SAME_TOOL}번 호출했다. 다른 근거로 판단한다")
        counts[name] = counts.get(name, 0) + 1

        tool = self.by_name[name]
        try:
            args = harness.enforce_tenant_datasource(tool, args, self.tenant_id)
        except harness.HarnessLimit as e:
            return self._error(call_id, name, str(e))
        if incident_id is not None and "incident_id" in args:
            args = {**args, "incident_id": incident_id}  # 모델이 다른 인시던트 번호를 넣지 못하게 한다

        await backend.emit(self.task_id, "TOOL_START", {"tool": name, "args": args})
        output = await tool.ainvoke(args)  # MCP 오류는 어댑터가 문자열로 돌려준다 (handle_tool_errors)
        text = harness.truncate(harness.mask(_as_text(output)))
        await backend.emit(self.task_id, "TOOL_END", {"tool": name})
        return ToolMessage(content=harness.as_untrusted(text), tool_call_id=call_id, name=name)

    @staticmethod
    def _error(call_id: str, name: str, message: str) -> ToolMessage:
        return ToolMessage(content=f"[Harness] {message}", tool_call_id=call_id, name=name, status="error")


def _as_text(output: Any) -> str:
    """MCP 도구 결과(content 블록 목록)를 문자열로 바꾼다."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(block.get("text", json.dumps(block, ensure_ascii=False)) if isinstance(block, dict)
                         else str(block) for block in output)
    return json.dumps(output, ensure_ascii=False, default=str)


def parse_json(message: ToolMessage) -> Any:
    """<tool_output> 경계를 벗겨 JSON으로 읽는다. JSON이 아니면 문자열 그대로 돌려준다."""
    text = str(message.content).removeprefix("<tool_output>\n").removesuffix("\n</tool_output>")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text

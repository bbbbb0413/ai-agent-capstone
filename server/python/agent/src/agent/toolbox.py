"""MCP 도구 연결과 Harness가 적용된 도구 실행기.

모든 도구 호출은 ToolBox.call을 거친다. 여기서 Node별 허용목록, 같은 도구 횟수 한도, 테넌트 데이터소스 강제,
민감정보 마스킹, 결과 길이 제한, 진행 이벤트 발행을 한 번에 처리한다. 오류는 예외가 아니라 ToolMessage로 모델에 돌려준다.
"""

import json
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent import harness
from agent.backend import backend
from agent.config import settings


async def load_tools(tenant_id: str, task_id: str, trace_id: str, task_token: str, *,
                     allow_partial: bool = False) -> list[BaseTool]:
    """세 MCP Server의 도구를 LangChain 도구로 받는다. 고객사·추적 정보는 요청 헤더로 싣는다.

    고객사는 작업 토큰(X-Task-Token)이 정한다. business-mcp는 그대로 backend에 넘기고 rag-mcp는 검증해 쓴다.
    X-Tenant-Id는 로그·추적용으로만 남는다.
    """
    headers = {
        "Authorization": f"Bearer {settings.agent_mcp_token}",
        "X-Task-Token": task_token,
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
    if not allow_partial:
        return await client.get_tools()
    # 첨부 자체는 모니터링 장애 중에도 읽는다. 빠진 자료는 ToolBox error로 결과에 표시한다.
    import asyncio
    async def fetch(server):
        return await asyncio.wait_for(client.get_tools(server_name=server), timeout=8)
    results = await asyncio.gather(*(fetch(s) for s in ("business", "rag", "grafana")), return_exceptions=True)
    return [tool for result in results if isinstance(result, list) for tool in result]


class ToolBox:
    def __init__(self, tools: list[BaseTool], tenant_id: str, task_id: str) -> None:
        self.by_name = {tool.name: tool for tool in tools}
        self.tenant_id = tenant_id
        self.task_id = task_id

    def for_node(self, node: str) -> list[BaseTool]:
        return [tool for name, tool in self.by_name.items() if name in harness.NODE_TOOLS[node]]

    async def call(self, node: str, name: str, args: dict[str, Any], counts: dict[str, int],
                   call_id: str = "direct", incident_id: int | None = None,
                   max_chars: int = harness.TOOL_RESULT_CHARS) -> ToolMessage:
        """counts(Node·도구별 호출 횟수)는 호출한 쪽이 State에 저장한다.

        max_chars: 결과 길이 제한. 모델에 바로 넘기지 않고 코드가 먼저 요약하는 결과만 늘린다(진단 로그 미리 조회).
        """
        if name not in harness.NODE_TOOLS[node] or name not in self.by_name:
            return self._error(call_id, name, f"'{name}'은 이 단계({node})에서 쓸 수 없는 도구다")
        key = f"{node}:{name}"  # 한도는 Node마다 따로 센다. 진단에서 다 쓰면 회복 확인에서 조회할 수 없다
        if counts.get(key, 0) >= harness.MAX_SAME_TOOL:
            return self._error(call_id, name, f"'{name}'을 이 단계에서 이미 {harness.MAX_SAME_TOOL}번 호출했다. "
                                              "다른 근거로 판단한다")
        counts[key] = counts.get(key, 0) + 1

        tool = self.by_name[name]
        try:
            args = harness.enforce_tenant_datasource(tool, args, self.tenant_id)
        except harness.HarnessLimit as e:
            return self._error(call_id, name, str(e))
        if incident_id is not None and "incident_id" in args:
            args = {**args, "incident_id": incident_id}  # 모델이 다른 인시던트 번호를 넣지 못하게 한다

        call_id = uuid4().hex if call_id == "direct" else call_id
        event = {"tool": name, "node": node, "callId": call_id}
        await backend.emit(self.task_id, "TOOL_START", {**event, "args": args})
        try:
            # ToolCall 형태로 호출해야 어댑터가 MCP의 isError와 artifact를 보존한다.
            output = await tool.ainvoke({"type": "tool_call", "name": name, "args": args, "id": call_id})
        except Exception as e:
            await backend.emit(self.task_id, "TOOL_END", {
                **event, "status": "error", "error": harness.truncate(harness.mask(str(e)), 500),
            })
            raise  # 연결 오류 등은 기존 Runner 재시도 정책으로 처리한다
        status = output.status if isinstance(output, ToolMessage) else "success"
        content = output.content if isinstance(output, ToolMessage) else output
        text = harness.truncate(harness.mask(_as_text(content)), max_chars)
        await backend.emit(self.task_id, "TOOL_END", {
            **event, "status": status, **({"error": text[:500]} if status == "error" else {}),
        })
        return ToolMessage(content=harness.as_untrusted(text), tool_call_id=call_id, name=name,
                           status=status)

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

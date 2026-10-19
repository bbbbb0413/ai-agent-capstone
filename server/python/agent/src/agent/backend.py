"""agent → backend 내부 API. agent는 DB에 업무 데이터를 쓰지 않고, 결과는 backend가 저장한다."""

from typing import Any

import httpx

from agent.config import settings


class Backend:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            base_url=settings.backend_url,
            headers={"Authorization": f"Bearer {settings.service_token}"},
            timeout=10,
        )

    async def emit(self, task_id: str, event_type: str, data: dict[str, Any]) -> None:
        """진행 이벤트(TOKEN, STEP, TOOL_START, TOOL_END, APPROVAL_REQUIRED, CHANGE_PENDING).
        backend가 SSE로 화면에 중계하고, TOKEN을 뺀 나머지는 진행 이력으로 저장한다."""
        await self.http.post(f"/internal/tasks/{task_id}/events", json={"type": event_type, "data": data})

    async def result(self, task_id: str, *, status: str, answer: str | None, sources: list[dict] | None = None,
                     summary: dict | None = None, report: str | None = None, trace_url: str | None = None) -> None:
        await self.http.post(f"/internal/tasks/{task_id}/result", json={
            "status": status, "answer": answer, "sources": sources, "summary": summary, "report": report,
            "traceUrl": trace_url,
        })

    async def close(self) -> None:
        await self.http.aclose()


backend = Backend()

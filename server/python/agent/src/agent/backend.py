"""agent → backend 내부 API. agent는 DB에 업무 데이터를 쓰지 않고, 결과는 backend가 저장한다.

응답이 4xx·5xx면 예외로 올린다. 상태를 보지 않으면 결과 저장이 실패해도 작업이 화면에서 RUNNING으로 남는다.
"""

import asyncio
import logging
from typing import Any

import httpx

from agent.config import settings

log = logging.getLogger(__name__)


class TaskFinished(Exception):
    """backend가 작업 토큰을 주지 않았다 (409). 끝났거나 사람을 기다리는 작업이라 실행하지 않는다."""


class Backend:
    # 결과는 작업의 끝이라 잃으면 되살릴 곳이 없다. 연결 실패와 5xx는 이만큼 기다리며 다시 보낸다
    RESULT_RETRY_SECONDS = (1.0, 2.0)

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            base_url=settings.backend_url,
            headers={"Authorization": f"Bearer {settings.agent_backend_token}"},
            timeout=10,
        )

    async def task_token(self, task_id: str) -> str:
        """실행할 때마다 작업 토큰을 받는다. 고객사는 backend가 그 작업의 값으로 정한다 (SPEC-limitations-fixes 3단계).

        작업 메시지(Kafka)에 싣지 않는다. 재시도·소비 지연·DLT 재처리에서 만료된 토큰으로 실행하지 않게 한다.
        """
        response = await self.http.post(f"/internal/tasks/{task_id}/token")
        if response.status_code == 409:
            raise TaskFinished(response.text)
        response.raise_for_status()
        return response.json()["token"]

    async def emit(self, task_id: str, event_type: str, data: dict[str, Any]) -> None:
        """진행 이벤트(TOKEN, STEP, TOOL_START, TOOL_END, APPROVAL_REQUIRED, CHANGE_PENDING).
        backend가 SSE로 화면에 중계하고, TOKEN을 뺀 나머지는 진행 이력으로 저장한다."""
        response = await self.http.post(f"/internal/tasks/{task_id}/events", json={"type": event_type, "data": data})
        response.raise_for_status()

    async def result(self, task_id: str, *, status: str, answer: str | None, sources: list[dict] | None = None,
                     summary: dict | None = None, report: str | None = None, trace_url: str | None = None) -> None:
        body = {"status": status, "answer": answer, "sources": sources, "summary": summary, "report": report,
                "traceUrl": trace_url}
        for delay in (*self.RESULT_RETRY_SECONDS, None):
            try:
                response = await self.http.post(f"/internal/tasks/{task_id}/result", json=body)
                response.raise_for_status()
                return
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                client_error = isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500
                if client_error or delay is None:   # 4xx는 다시 보내도 같다
                    raise
                log.warning("task %s result delivery failed (%s), retrying in %.0fs", task_id, e, delay)
                await asyncio.sleep(delay)

    async def attachments(self, task_id: str, tenant_id: str) -> list[dict]:
        response = await self.http.get(f"/internal/tasks/{task_id}/attachments", headers={"X-Tenant-Id": tenant_id})
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.http.aclose()


backend = Backend()

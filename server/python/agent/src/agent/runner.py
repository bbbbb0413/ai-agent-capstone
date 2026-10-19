"""작업 실행기. Graph를 interrupt나 종료까지 돌리고, 결과를 backend에 넘긴다.

- 실행 시간 한도(120초)는 구간별로 잰다. 승인을 기다리는 시간은 들어가지 않는다.
  누적 시간은 checkpointer와 별도로 agent 스키마의 task_budget 테이블에 둔다.
- 같은 task_id가 다시 오면(Kafka 중복 소비) 이미 checkpoint가 있으므로 건너뛴다 (멱등).
"""

import asyncio
import logging
import time
from typing import Any

from langgraph.types import Command
from psycopg_pool import AsyncConnectionPool

from agent import harness
from agent.backend import backend
from agent.graph import Context
from agent.toolbox import ToolBox, load_tools

log = logging.getLogger(__name__)


class Runner:
    def __init__(self, graph, pool: AsyncConnectionPool) -> None:
        self.graph = graph
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS task_budget (
                    task_id         TEXT PRIMARY KEY,
                    elapsed_seconds DOUBLE PRECISION NOT NULL DEFAULT 0
                )
            """)

    async def run(self, message: dict[str, Any], *, raise_errors: bool = False, retry: bool = False) -> str:
        """retry=True(Kafka retry 토픽)이면 실패한 Node부터 checkpoint로 이어서 실행한다."""
        task_id = str(message["taskId"])
        config = self._config(task_id, message["tenantId"], message["traceId"])
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            if retry and snapshot.next and not snapshot.interrupts:
                log.info("task %s retrying from checkpoint at %s", task_id, snapshot.next)
                return await self._drive(task_id, None, snapshot.values, config, raise_errors)
            log.info("task %s already started, skipping duplicate", task_id)
            return "DUPLICATE"
        state = {
            "task_id": task_id, "tenant_id": message["tenantId"], "incident_id": message.get("incidentId"),
            "type": message["type"], "question": message.get("question") or "", "trace_id": message["traceId"],
        }
        return await self._drive(task_id, state, state, config, raise_errors)

    async def resume(self, task_id: str, decision: dict[str, Any]) -> str:
        snapshot = await self.graph.aget_state({"configurable": {"thread_id": task_id}})
        values = snapshot.values
        config = self._config(task_id, values["tenant_id"], values["trace_id"])
        return await self._drive(task_id, Command(resume=decision), values, config, raise_errors=False)

    async def _drive(self, task_id: str, payload: Any, values: dict, config: dict, raise_errors: bool) -> str:
        elapsed = await self._elapsed(task_id)
        started = time.monotonic()
        try:
            tools = await load_tools(values["tenant_id"], task_id, values["trace_id"])
            context = Context(toolbox=ToolBox(tools, values["tenant_id"], task_id), elapsed_before=elapsed)
            remaining = max(1.0, harness.TASK_SECONDS - elapsed)
            result = await asyncio.wait_for(self.graph.ainvoke(payload, config, context=context), timeout=remaining)
        except (TimeoutError, harness.HarnessLimit) as e:
            await backend.result(task_id, status="FAILED", answer=f"Harness 한도로 중단했다: {e or '시간 초과'}")
            return "FAILED"
        except Exception as e:
            log.exception("task %s failed", task_id)
            if raise_errors:
                raise  # Kafka 경로: 호출한 쪽이 retry 토픽으로 보낸다
            await backend.result(task_id, status="FAILED", answer=f"실행 중 오류: {type(e).__name__}")
            return "FAILED"
        finally:
            await self._add_elapsed(task_id, time.monotonic() - started)

        if interrupts := result.get("__interrupt__"):
            await backend.emit(task_id, "APPROVAL_REQUIRED", interrupts[0].value)
            return "WAITING_APPROVAL"

        sources = [{"doc": d.get("uri"), "section": d.get("section")} for d in result.get("runbook", [])]
        # TODO: LangSmith trace 링크를 trace_id로 찾아 trace_url에 넣는다
        await backend.result(task_id, status="DONE", answer=result.get("report"), sources=sources,
                             report=result.get("report"))
        return "DONE"

    @staticmethod
    def _config(task_id: str, tenant_id: str, trace_id: str) -> dict:
        # LangSmith에서 고객사·trace_id로 찾을 수 있게 태그와 메타데이터를 단다
        return {"configurable": {"thread_id": task_id}, "tags": [f"tenant:{tenant_id}"],
                "metadata": {"tenant_id": tenant_id, "task_id": task_id, "trace_id": trace_id},
                "run_name": "opslens-task"}

    async def _elapsed(self, task_id: str) -> float:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT elapsed_seconds FROM task_budget WHERE task_id = %s", (task_id,))).fetchone()
        return row["elapsed_seconds"] if row else 0.0  # 연결 풀은 dict_row를 쓴다

    async def _add_elapsed(self, task_id: str, seconds: float) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("""
                INSERT INTO task_budget (task_id, elapsed_seconds) VALUES (%s, %s)
                ON CONFLICT (task_id) DO UPDATE SET elapsed_seconds = task_budget.elapsed_seconds + EXCLUDED.elapsed_seconds
            """, (task_id, seconds))

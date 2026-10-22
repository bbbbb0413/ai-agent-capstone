"""작업 실행기. Graph를 interrupt나 종료까지 돌리고, 결과를 backend에 넘긴다.

- 실행 시간 한도(120초)는 구간별로 잰다. 승인·반영(PR 병합)을 기다리는 시간과 회복 확인 대기는 들어가지 않는다.
  누적 시간은 checkpointer와 별도로 agent 스키마의 task_budget 테이블에 둔다.
- 실행 임대 (SPEC-kafka-scale 4.3(6)): 실행하는 파드가 task_budget에 owner와 heartbeat_at을 남기고 15초마다 갱신한다.
  같은 task_id가 다시 오면(Kafka 중복 소비·리밸런싱) 임대가 살아 있으면 건너뛰고, 끊겼으면 checkpoint에서 이어 간다.
  파드를 늘릴 때의 리밸런싱은 실행 중인 파드가 살아 있는데도 파티션을 넘기므로, "내 프로세스에서 실행 중이 아니다"만으로
  이어 가면 같은 작업이 두 파드에서 동시에 돈다.
- 임대가 끊긴 작업(파드가 죽음, 종료 신호로 중단)은 stale()로 찾아 recover()가 이어 간다. main이 주기적으로 부른다.
- 스키마(task_budget)는 agent-migrate가 만든다. 이 모듈은 DDL을 하지 않는다.
"""

import asyncio
import logging
import os
import socket
import time
import uuid
from weakref import WeakValueDictionary
from typing import Any

from langgraph.types import Command
from openai import AuthenticationError
from psycopg_pool import AsyncConnectionPool

from agent import harness, summary
from agent.backend import backend
from agent.graph import Context
from agent.toolbox import ToolBox, load_tools

log = logging.getLogger(__name__)

LEASE_SECONDS = 60       # 이 시간 동안 갱신이 없으면 실행하던 파드가 죽은 것으로 본다
HEARTBEAT_SECONDS = 15   # 실행 중 임대 갱신 주기. LEASE_SECONDS보다 충분히 짧아야 한다
# 파드(프로세스)마다 다르다. 컨테이너가 재시작되면 hostname과 PID 1이 같아서 둘만으로는 이전 프로세스와 구분되지 않는다
OWNER = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class Runner:
    def __init__(self, graph, pool: AsyncConnectionPool) -> None:
        self.graph = graph
        self.pool = pool
        self._resume_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._running: set[str] = set()   # 이 프로세스가 지금 실행 중인 작업

    async def stale(self) -> list[str]:
        """실행 중으로 표시됐지만 임대가 끊긴 작업 (기획안 8장 Recovery: checkpointer에서 재개).

        임대 열이 생기기 전에 남은 행(heartbeat_at NULL)과 종료 신호로 임대를 놓은 행도 여기에 든다.
        """
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT task_id FROM task_budget WHERE running "
                "AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => %s))",
                (LEASE_SECONDS,))).fetchall()
        return [row["task_id"] for row in rows if row["task_id"] not in self._running]

    async def recover(self, task_id: str) -> str:
        snapshot = await self.graph.aget_state({"configurable": {"thread_id": task_id}})
        if not snapshot.values or not snapshot.next or snapshot.interrupts:
            await self._clear_stale(task_id)  # 이미 끝났거나 사람을 기다리는 중이다
            return "SKIPPED"
        log.info("task %s recovering from checkpoint at %s", task_id, snapshot.next)
        values = snapshot.values
        config = self._config(task_id, values["tenant_id"], values["trace_id"])
        return await self._drive(task_id, None, values, config, raise_errors=False)

    async def run(self, message: dict[str, Any], *, raise_errors: bool = False) -> str:
        """작업을 실행한다. 이미 시작한 작업이 다시 오면(재시도 토픽, 중복 소비, 리밸런싱):

        - 끝났거나 사람을 기다리는 중이면 건너뛴다 (DUPLICATE)
        - 멈춘 Node가 남아 있으면 checkpoint에서 이어 간다. 다른 파드가 임대를 쥐고 있으면 _drive가 건너뛴다
        """
        task_id = str(message["taskId"])
        config = self._config(task_id, message["tenantId"], message["traceId"])
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            if snapshot.next and not snapshot.interrupts:
                log.info("task %s continuing from checkpoint at %s", task_id, snapshot.next)
                return await self._drive(task_id, None, snapshot.values, config, raise_errors)
            log.info("task %s already finished or waiting, skipping duplicate", task_id)
            return "DUPLICATE"
        state = {
            "task_id": task_id, "tenant_id": message["tenantId"], "incident_id": message.get("incidentId"),
            "type": message["type"], "question": message.get("question") or "", "trace_id": message["traceId"],
        }
        return await self._drive(task_id, state, state, config, raise_errors)

    async def resume(self, task_id: str, decision: dict[str, Any]) -> str:
        # 전달 응답 유실 후 재시도나 동시 승인에서도 같은 interrupt를 한 번만 소비한다.
        lock = self._resume_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            snapshot = await self.graph.aget_state({"configurable": {"thread_id": task_id}})
            if not snapshot.interrupts:
                return "DUPLICATE"
            approval_id = decision.get("approvalId")
            if approval_id and approval_id != snapshot.interrupts[0].value.get("approvalId"):
                return "DUPLICATE"
            values = snapshot.values
            config = self._config(task_id, values["tenant_id"], values["trace_id"])
            return await self._drive(task_id, Command(resume=decision), values, config, raise_errors=False)

    async def _drive(self, task_id: str, payload: Any, values: dict, config: dict, raise_errors: bool) -> str:
        if task_id in self._running or not await self._claim(task_id):
            log.info("task %s is running elsewhere, skipping", task_id)
            return "RUNNING_ELSEWHERE"
        self._running.add(task_id)
        heartbeat = asyncio.create_task(self._heartbeat(task_id))
        try:
            return await self._drive_claimed(task_id, payload, values, config, raise_errors)
        finally:
            heartbeat.cancel()
            self._running.discard(task_id)

    async def _drive_claimed(self, task_id: str, payload: Any, values: dict, config: dict, raise_errors: bool) -> str:
        elapsed = await self._elapsed(task_id)
        started = time.monotonic()
        context: Context | None = None
        finished = True  # 종료 신호로 끊긴 경우만 False로 두고, 임대를 놓아 다른(또는 다시 뜬) 파드가 바로 이어 간다
        try:
            if values.get("type") == "ATTACHMENT":
                tools = await load_tools(values["tenant_id"], task_id, values["trace_id"], allow_partial=True)
            else:
                tools = await load_tools(values["tenant_id"], task_id, values["trace_id"])
            context = Context(toolbox=ToolBox(tools, values["tenant_id"], task_id), elapsed_before=elapsed)
            # 회복 확인 대기(verify, 재진입 포함 최대 2번)는 한도에서 빠지므로 그만큼 여유를 둔다
            remaining = max(1.0, harness.TASK_SECONDS - elapsed) + 2 * (harness.VERIFY_SETTLE_SECONDS
                                                                         + harness.VERIFY_RETRY_SECONDS
                                                                         + harness.VERIFY_CONFIRM_SECONDS)
            result = await asyncio.wait_for(self.graph.ainvoke(payload, config, context=context), timeout=remaining)
        except asyncio.CancelledError:
            finished = False
            raise
        except (TimeoutError, harness.HarnessLimit) as e:
            await backend.result(task_id, status="FAILED", answer=f"Harness 한도로 중단했다: {str(e) or '시간 초과'}")
            return "FAILED"
        except AuthenticationError:
            # 키 오류는 재전송으로 해결되지 않는다. 응답 본문/키를 로그에 남기지 않는다.
            log.warning("task %s OpenAI authentication failed", task_id)
            await backend.result(task_id, status="FAILED", answer=(
                "OpenAI API 키 인증에 실패했습니다. Agent의 OPENAI_API_KEY를 유효한 OpenAI 키로 설정하고 "
                "Agent를 다시 실행해 주세요. Jev 키는 이 설정에 사용할 수 없습니다."))
            return "FAILED"
        except Exception as e:
            log.exception("task %s failed", task_id)
            if raise_errors:
                raise  # Kafka 경로: 호출한 쪽이 retry 토픽으로 보낸다
            await backend.result(task_id, status="FAILED", answer=f"실행 중 오류: {type(e).__name__}")
            return "FAILED"
        finally:
            await self._add_elapsed(task_id, time.monotonic() - started - (context.waited if context else 0.0))
            await (self._release(task_id) if finished else self._abandon(task_id))

        if interrupts := result.get("__interrupt__"):
            value = interrupts[0].value
            if value.get("kind") == "CHANGE":  # GitOps PR 병합·동기화 대기 (graph.await_change)
                await backend.emit(task_id, "CHANGE_PENDING", value)
                return "CHANGE_PENDING"
            await backend.emit(task_id, "APPROVAL_REQUIRED", value)
            return "WAITING_APPROVAL"

        sources = [{"doc": uri, "section": section} for uri, section in dict.fromkeys(
            (d.get("uri"), d.get("section"))
            for d in result.get("runbook", []) + result.get("past_incidents", []))]
        # TODO: LangSmith trace 링크를 trace_id로 찾아 trace_url에 넣는다
        await backend.result(task_id, status="DONE", answer=result.get("report"), sources=sources,
                             summary=summary.build(result), report=result.get("report"))
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

    async def _claim(self, task_id: str) -> bool:
        """실행 임대를 잡는다. 아무도 실행하지 않거나 임대가 끊겼을 때만 잡힌다 (한 문장이라 동시에 둘이 잡지 못한다)."""
        async with self.pool.connection() as conn:
            row = await (await conn.execute("""
                INSERT INTO task_budget (task_id, running, owner, heartbeat_at) VALUES (%s, true, %s, now())
                ON CONFLICT (task_id) DO UPDATE SET running = true, owner = EXCLUDED.owner, heartbeat_at = now()
                WHERE NOT task_budget.running OR task_budget.heartbeat_at IS NULL
                   OR task_budget.heartbeat_at < now() - make_interval(secs => %s)
                RETURNING task_id
            """, (task_id, OWNER, LEASE_SECONDS))).fetchone()
        return row is not None

    async def _heartbeat(self, task_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with self.pool.connection() as conn:
                    await conn.execute("UPDATE task_budget SET heartbeat_at = now() WHERE task_id = %s AND owner = %s",
                                       (task_id, OWNER))
            except Exception:  # noqa: BLE001 — 갱신이 한 번 빠져도 임대 시간(60초) 안에서는 괜찮다
                log.warning("task %s heartbeat failed", task_id, exc_info=True)

    async def _release(self, task_id: str) -> None:
        """끝났거나 사람을 기다리는 중이다. 이어 갈 것이 없다."""
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE task_budget SET running = false WHERE task_id = %s AND owner = %s",
                               (task_id, OWNER))

    async def _clear_stale(self, task_id: str) -> None:
        """이어 갈 것이 없는데 running으로 남은 행을 정리한다. 임대가 살아 있는 행은 건드리지 않는다."""
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE task_budget SET running = false WHERE task_id = %s AND (heartbeat_at IS NULL "
                               "OR heartbeat_at < now() - make_interval(secs => %s))", (task_id, LEASE_SECONDS))

    async def _abandon(self, task_id: str) -> None:
        """종료 신호로 중단됐다. running은 남기고 임대만 놓아 stale()이 바로 찾게 한다."""
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE task_budget SET heartbeat_at = NULL WHERE task_id = %s AND owner = %s",
                               (task_id, OWNER))

    async def _add_elapsed(self, task_id: str, seconds: float) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("""
                INSERT INTO task_budget (task_id, elapsed_seconds) VALUES (%s, %s)
                ON CONFLICT (task_id) DO UPDATE SET elapsed_seconds = task_budget.elapsed_seconds + EXCLUDED.elapsed_seconds
            """, (task_id, seconds))

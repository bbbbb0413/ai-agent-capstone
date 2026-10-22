"""agent 서버. backend만 호출할 수 있다 (서비스 토큰).

POST /agent/tasks/{id}/run     작업 실행 (DISPATCH_MODE=http일 때 backend가 호출. 기본 kafka에서는 Kafka 소비자가 실행한다)
POST /agent/tasks/{id}/resume  승인·거절 후 재개 (항상 HTTP)
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from agent import graph as graph_module
from agent import kafka_worker
from agent.backend import backend
from agent.config import settings
from agent.runner import Runner

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()
_slots = asyncio.Semaphore(settings.workers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # check: postgres가 재시작되면 풀에 남은 커넥션이 끊긴다. 꺼낼 때 확인해서 새로 연결한다
    pool = AsyncConnectionPool(settings.database_url, open=False, check=AsyncConnectionPool.check_connection,
                               kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await pool.open()
    # 스키마(checkpointer 표, task_budget)는 agent-migrate가 먼저 만든다. 여기서는 DDL을 하지 않는다
    app.state.runner = runner = Runner(graph_module.build(AsyncPostgresSaver(pool)), pool)
    _spawn(_recover_stale(runner))
    if settings.dispatch_mode == "kafka":
        _spawn(kafka_worker.consume(runner, _slots))
    yield
    for task in _background:
        task.cancel()
    await backend.close()
    await pool.close()


app = FastAPI(title="OpsLens agent", lifespan=lifespan)


def require_service_token(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {settings.service_token}"
    if not settings.service_token or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid service token")


class TaskMessage(BaseModel):
    taskId: str
    tenantId: str
    slaTier: str
    incidentId: int | None = None
    type: str
    question: str | None = None
    traceId: str


class ResumeRequest(BaseModel):
    decision: str
    comment: str | None = None
    approver: str
    approvalId: str | None = None


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


RECOVER_INTERVAL_SECONDS = 30


async def _recover_stale(runner: Runner) -> None:
    """임대가 끊긴 작업을 checkpoint에서 이어 간다. 시작할 때와 그 뒤 주기적으로 본다.

    시작할 때 한 번만 보면, 다른 파드가 죽고 다시 뜨지 않을 때 그 파드의 작업이 남는다 (SPEC-kafka-scale 4.3(6)).
    """
    recovering: set[str] = set()   # 슬롯을 기다리는 복구를 다음 주기에 또 띄우지 않는다

    async def recover(task_id: str) -> None:
        try:
            await _limited(task_id, runner.recover(task_id))
        finally:
            recovering.discard(task_id)

    while True:
        try:
            for task_id in set(await runner.stale()) - recovering:
                recovering.add(task_id)
                _spawn(recover(task_id))
        except Exception:  # noqa: BLE001 — DB가 잠깐 안 되면 다음 주기에 다시 본다
            log.warning("stale task check failed", exc_info=True)
        await asyncio.sleep(RECOVER_INTERVAL_SECONDS)


async def _limited(task_id: str, coro) -> Any:
    """Graph에 들어가기 전(checkpoint 조회 등)에 실패해도 작업이 QUEUED로 남지 않게 FAILED를 알린다."""
    async with _slots:
        try:
            return await coro
        except Exception as e:
            log.exception("task %s failed", task_id)
            await backend.result(task_id, status="FAILED", answer=f"실행 중 오류: {type(e).__name__}")


@app.get("/health")
async def health(response: Response) -> dict:
    """kafka 모드에서는 소비 루프가 최근에 poll했는지도 본다. 소비가 멈춘 agent를 정상으로 보이지 않게 한다."""
    if settings.dispatch_mode == "kafka" and not kafka_worker.health.alive():
        response.status_code = 503
        return {"status": "degraded", "kafka": kafka_worker.health.error or "no recent poll"}
    return {"status": "ok"}


@app.post("/agent/tasks/{task_id}/run", status_code=202, dependencies=[Depends(require_service_token)])
async def run(task_id: str, message: TaskMessage) -> dict:
    _spawn(_limited(task_id, app.state.runner.run(message.model_dump())))
    return {"taskId": task_id}


@app.post("/agent/tasks/{task_id}/resume", status_code=202, dependencies=[Depends(require_service_token)])
async def resume(task_id: str, request: ResumeRequest) -> dict:
    _spawn(_limited(task_id, app.state.runner.resume(task_id, request.model_dump())))
    return {"taskId": task_id}

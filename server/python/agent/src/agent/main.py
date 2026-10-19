"""agent 서버. backend만 호출할 수 있다 (서비스 토큰).

POST /agent/tasks/{id}/run     작업 실행 (DISPATCH_MODE=http일 때 backend가 호출)
POST /agent/tasks/{id}/resume  승인·거절 후 재개 (항상 HTTP)
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
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
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    app.state.runner = runner = Runner(graph_module.build(checkpointer), pool)
    await runner.setup()
    if settings.dispatch_mode == "kafka":
        _spawn(kafka_worker.consume(runner))
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


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _limited(task_id: str, coro) -> Any:
    """Graph에 들어가기 전(checkpoint 조회 등)에 실패해도 작업이 QUEUED로 남지 않게 FAILED를 알린다."""
    async with _slots:
        try:
            return await coro
        except Exception as e:
            log.exception("task %s failed", task_id)
            await backend.result(task_id, status="FAILED", answer=f"실행 중 오류: {type(e).__name__}")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/agent/tasks/{task_id}/run", status_code=202, dependencies=[Depends(require_service_token)])
async def run(task_id: str, message: TaskMessage) -> dict:
    _spawn(_limited(task_id, app.state.runner.run(message.model_dump())))
    return {"taskId": task_id}


@app.post("/agent/tasks/{task_id}/resume", status_code=202, dependencies=[Depends(require_service_token)])
async def resume(task_id: str, request: ResumeRequest) -> dict:
    _spawn(_limited(task_id, app.state.runner.resume(task_id, request.model_dump())))
    return {"taskId": task_id}

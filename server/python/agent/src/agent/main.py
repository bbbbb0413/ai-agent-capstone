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

_background: set[asyncio.Task] = set()
_slots = asyncio.Semaphore(settings.workers)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(settings.database_url, open=False,
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


async def _limited(coro) -> Any:
    async with _slots:
        return await coro


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/agent/tasks/{task_id}/run", status_code=202, dependencies=[Depends(require_service_token)])
async def run(task_id: str, message: TaskMessage) -> dict:
    _spawn(_limited(app.state.runner.run(message.model_dump())))
    return {"taskId": task_id}


@app.post("/agent/tasks/{task_id}/resume", status_code=202, dependencies=[Depends(require_service_token)])
async def resume(task_id: str, request: ResumeRequest) -> dict:
    _spawn(_limited(app.state.runner.resume(task_id, request.model_dump())))
    return {"taskId": task_id}

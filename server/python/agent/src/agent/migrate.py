"""agent 스키마 마이그레이션. agent-migrate 컨테이너가 agent보다 먼저 한 번 실행하고 끝난다.

    python -m agent.migrate

agent 서버는 기동할 때 DDL을 하지 않는다. 파드 여러 개가 동시에 DDL을 하면 카탈로그 잠금이 겹치고
CREATE TABLE IF NOT EXISTS끼리도 중복 키 오류가 날 수 있다 (SPEC-kafka-scale 4.3(8)).
"""

import asyncio
import logging

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def upgrade_schema() -> None:
    config = Config()
    config.set_main_option("script_location", settings.alembic_dir)
    # SQLAlchemy에 psycopg 3 드라이버를 쓰라고 알린다 (기본은 psycopg2)
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    command.upgrade(config, "head")


async def setup_checkpointer() -> None:
    """LangGraph checkpointer 테이블 (agent.checkpoints*). 라이브러리가 자기 버전 표로 관리한다."""
    async with AsyncConnectionPool(settings.database_url, open=False,
                                   kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}) as pool:
        await AsyncPostgresSaver(pool).setup()


def main() -> None:
    upgrade_schema()
    asyncio.run(setup_checkpointer())
    log.info("agent schema is up to date")


if __name__ == "__main__":
    main()

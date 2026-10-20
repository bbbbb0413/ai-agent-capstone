"""agent 스키마 마이그레이션 실행 환경. agent.migrate가 sqlalchemy.url을 넣어 부른다 (alembic.ini를 두지 않는다)."""

from alembic import context
from sqlalchemy import create_engine

engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
with engine.connect() as connection:
    # opslens_agent 계정의 search_path가 agent라 테이블은 agent 스키마에 생긴다. 버전 표도 같은 곳에 둔다
    context.configure(connection=connection, version_table_schema="agent")
    with context.begin_transaction():
        context.run_migrations()

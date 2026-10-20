"""작업별 누적 실행 시간과 실행 중 표시 (runner.setup()이 기동 때마다 하던 DDL).

이미 테이블이 있는 DB에서도 돌 수 있게 IF NOT EXISTS로 쓴다.
"""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_budget (
            task_id         TEXT PRIMARY KEY,
            elapsed_seconds DOUBLE PRECISION NOT NULL DEFAULT 0
        )
    """)
    op.execute("ALTER TABLE task_budget ADD COLUMN IF NOT EXISTS running BOOLEAN NOT NULL DEFAULT false")

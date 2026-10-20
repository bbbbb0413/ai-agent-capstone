"""실행 임대: 어느 파드가 실행 중인지와 마지막 갱신 시각 (SPEC-kafka-scale 4.3(6)).

임대가 살아 있으면 다른 파드가 같은 작업을 동시에 돌리지 않고, 끊기면(파드가 죽음) 다른 파드가 이어 간다.
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("ALTER TABLE task_budget ADD COLUMN IF NOT EXISTS owner TEXT")
    op.execute("ALTER TABLE task_budget ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ")

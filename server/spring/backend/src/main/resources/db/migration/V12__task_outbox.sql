-- 작업 전달 대기열 (Transactional Outbox, SPEC-kafka-scale 4.2).
-- ai_tasks 생성과 같은 트랜잭션에 기록해 DB 커밋과 발행 사이의 유실(Dual-Write)을 막는다.
-- 발행은 TaskOutboxRelay가 TaskDispatcher(HTTP 또는 Kafka)로 한다. 토픽과 키는 발행기가 payload로 정한다.

CREATE TABLE task_outbox (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         UUID        NOT NULL UNIQUE REFERENCES ai_tasks (id),
    payload         JSONB       NOT NULL,                    -- TaskMessage
    trace_id        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'PENDING',  -- PENDING | SENT | FAILED
    attempts        INT         NOT NULL DEFAULT 0,          -- 발행을 시도한 횟수 (임대할 때 센다)
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 임대 중이면 임대가 끝나는 시각
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ
);

CREATE INDEX task_outbox_pending ON task_outbox (next_attempt_at, id) WHERE status = 'PENDING';

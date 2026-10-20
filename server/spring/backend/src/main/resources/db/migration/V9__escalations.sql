-- 에스컬레이션 발송 대기열 (outbox). 누구에게 무엇을 보낼지 정하고 메시지까지 만들어 저장한다.
-- 실제 발송(메신저·전화·메일)은 아직 없다. 발송기를 붙이면 QUEUED를 읽어 보내고 SENT 또는 FAILED로 바꾼다.

-- 계약상 승인이 필요한 조치를 요청할 고객사 담당 (RB-STD-06, B사 계약 "확장·롤백은 B사 승인")
ALTER TABLE tenants ADD COLUMN customer_contact TEXT;
UPDATE tenants SET customer_contact = 'A사 운영 담당' WHERE id = 'tenant-a';
UPDATE tenants SET customer_contact = 'B사 운영 승인 담당' WHERE id = 'tenant-b';

CREATE TABLE escalations (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        TEXT        NOT NULL REFERENCES tenants (id),
    incident_id      BIGINT      NOT NULL REFERENCES incidents (id),
    category         TEXT        NOT NULL,              -- P1 | NOT_RECOVERED | CHANGE_TIMEOUT | ACTION_BLOCKED | DEPENDENCY | CAUSE_UNKNOWN
    reason           TEXT        NOT NULL,
    service          TEXT,
    required_action  TEXT,                              -- ACTION_BLOCKED: 필요하지만 할 수 없는 조치
    blocked_by       TEXT,                              -- ACTION_BLOCKED: CONTRACT | STRUCTURE | RUNTIME
    dependency       TEXT,                              -- DEPENDENCY: 원인인 의존 대상 (service_dependencies.name)
    recipients       JSONB       NOT NULL,              -- [{kind, name, role}]
    message          TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'QUEUED',  -- QUEUED | SENT | FAILED (SENT·FAILED는 발송기가 쓴다)
    trace_id         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ
);
CREATE INDEX escalations_queued ON escalations (created_at) WHERE status = 'QUEUED';
CREATE INDEX escalations_incident ON escalations (incident_id);

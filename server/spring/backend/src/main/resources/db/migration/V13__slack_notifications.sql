-- 연결 저장만으로 기존 호출을 발송하지 않는다. 사용자 검토 후 승인한 건만 slack_deliveries에 넣는다.
CREATE TABLE slack_connections (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    channel_label TEXT NOT NULL,
    webhook_encrypted TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    version BIGINT NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE slack_deliveries (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    escalation_id BIGINT NOT NULL UNIQUE REFERENCES escalations(id),
    channel_label TEXT NOT NULL,
    connection_version BIGINT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INT NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    last_error TEXT,
    CHECK (status IN ('QUEUED', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN', 'CANCELLED'))
);
CREATE INDEX slack_deliveries_pending ON slack_deliveries(available_at) WHERE status = 'QUEUED';

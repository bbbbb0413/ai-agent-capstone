CREATE TABLE slack_tracking_settings (
    tenant_id TEXT PRIMARY KEY REFERENCES slack_connections(tenant_id),
    app_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    signing_secret_encrypted TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    version BIGINT NOT NULL DEFAULT 1
);
CREATE TABLE slack_members (
    tenant_id TEXT NOT NULL REFERENCES slack_tracking_settings(tenant_id),
    slack_user_id TEXT NOT NULL,
    username TEXT NOT NULL REFERENCES users(username),
    PRIMARY KEY (tenant_id, slack_user_id),
    UNIQUE (tenant_id, username)
);
ALTER TABLE slack_deliveries
    ADD COLUMN tracking_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN handling_status TEXT NOT NULL DEFAULT 'WAITING'
        CHECK (handling_status IN ('WAITING','ACKNOWLEDGED','IN_PROGRESS','COMPLETED')),
    ADD COLUMN acknowledged_by TEXT,
    ADD COLUMN acknowledged_at TIMESTAMPTZ,
    ADD COLUMN assigned_to TEXT,
    ADD COLUMN assigned_at TIMESTAMPTZ,
    ADD COLUMN completed_by TEXT,
    ADD COLUMN completed_at TIMESTAMPTZ,
    ADD COLUMN slack_message_ts TEXT;
CREATE TABLE slack_tracking_events (
    id BIGSERIAL PRIMARY KEY,
    delivery_id UUID NOT NULL REFERENCES slack_deliveries(id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX slack_tracking_events_delivery ON slack_tracking_events(delivery_id, id);
CREATE TABLE slack_interaction_receipts (
    digest TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Slack 원문 갱신은 수신 응답(3초 제한)과 분리한다. URL에는 비밀 토큰이 있어 암호화한다.
CREATE TABLE slack_tracking_updates (
    id BIGSERIAL PRIMARY KEY,
    delivery_id UUID NOT NULL REFERENCES slack_deliveries(id),
    url_encrypted TEXT NOT NULL,
    feedback TEXT,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    attempts INT NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '25 minutes',
    last_error TEXT
);
CREATE INDEX slack_tracking_updates_pending ON slack_tracking_updates(available_at) WHERE status='QUEUED';

-- 업무 데이터, 작업 이력, 감사 로그 (app 스키마, backend 전용)

CREATE TABLE tenants (
    id                   TEXT PRIMARY KEY,
    name                 TEXT        NOT NULL,
    sla_tier             TEXT        NOT NULL,           -- GOLD | SILVER
    availability_target  NUMERIC(5, 2) NOT NULL,         -- %
    response_minutes     INT         NOT NULL,           -- 계약상 응답 기한(분)
    app_url              TEXT        NOT NULL,           -- 조치 실행 대상 (demo-app)
    allowed_actions      TEXT[]      NOT NULL            -- 계약상 허용된 조치
);

CREATE TABLE services (
    tenant_id   TEXT NOT NULL REFERENCES tenants (id),
    name        TEXT NOT NULL,
    depends_on  TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE users (
    id         BIGSERIAL PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    role       TEXT NOT NULL,                             -- MSP_ADMIN | MSP_ENGINEER | TENANT_VIEWER
    tenant_id  TEXT REFERENCES tenants (id)               -- TENANT_VIEWER만 사용
);

-- MSP_ENGINEER가 담당하는 고객사
CREATE TABLE user_tenants (
    user_id    BIGINT NOT NULL REFERENCES users (id),
    tenant_id  TEXT   NOT NULL REFERENCES tenants (id),
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE incidents (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT        NOT NULL REFERENCES tenants (id),
    service      TEXT,
    title        TEXT        NOT NULL,
    severity     TEXT        NOT NULL,                    -- P1 | P2 | P3
    status       TEXT        NOT NULL DEFAULT 'OPEN',     -- OPEN | MITIGATED | RESOLVED
    fingerprint  TEXT,                                    -- Alertmanager 알림 식별자
    report       TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX incidents_open_fingerprint ON incidents (fingerprint) WHERE status = 'OPEN';

CREATE TABLE ai_tasks (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES tenants (id),
    incident_id     BIGINT REFERENCES incidents (id),
    type            TEXT        NOT NULL,                 -- DIAGNOSE | QUESTION | REINDEX
    question        TEXT,
    status          TEXT        NOT NULL,                 -- QUEUED | RUNNING | WAITING_APPROVAL | DONE | FAILED
    requested_by    BIGINT REFERENCES users (id),
    pending_action  JSONB,                                -- 승인 대기 중인 도구·인자
    answer          TEXT,
    sources         JSONB,
    trace_url       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id          BIGSERIAL PRIMARY KEY,
    task_id     UUID        NOT NULL REFERENCES ai_tasks (id),
    tool_name   TEXT        NOT NULL,
    args        JSONB       NOT NULL,
    decision    TEXT        NOT NULL,                     -- APPROVE | REJECT
    comment     TEXT,
    decided_by  BIGINT      NOT NULL REFERENCES users (id),
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT,
    actor       TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    detail      JSONB,
    trace_id    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

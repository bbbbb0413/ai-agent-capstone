-- 같은 서비스의 알림을 열린 인시던트 하나로 묶는다 (SPEC-compound-faults 5장).
-- 원인이 겹치면 HighErrorRate와 HighLatency가 함께 울린다. 알림마다 인시던트를 열면 작업 두 개가 같은 서비스를 동시에 조치한다.
-- 인시던트는 묶인 알림이 모두 해제될 때 닫힌다. incidents.fingerprint는 인시던트를 연 첫 알림으로 남긴다.

CREATE TABLE incident_alerts (
    id           BIGSERIAL   PRIMARY KEY,
    incident_id  BIGINT      NOT NULL REFERENCES incidents (id),
    fingerprint  TEXT        NOT NULL,          -- Alertmanager 알림 식별자
    alertname    TEXT,
    title        TEXT        NOT NULL,
    severity     TEXT        NOT NULL,          -- P1 | P2
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

-- 울리고 있는 같은 알림은 하나만 둔다 (Alertmanager가 같은 알림을 반복해서 보낸다)
CREATE UNIQUE INDEX incident_alerts_firing ON incident_alerts (fingerprint) WHERE resolved_at IS NULL;
CREATE INDEX incident_alerts_incident ON incident_alerts (incident_id);

-- 열린 알림 인시던트는 그 알림 하나가 묶인 상태로 옮긴다
INSERT INTO incident_alerts (incident_id, fingerprint, title, severity, started_at)
SELECT id, fingerprint, title, severity, started_at FROM incidents
WHERE status = 'OPEN' AND fingerprint IS NOT NULL;

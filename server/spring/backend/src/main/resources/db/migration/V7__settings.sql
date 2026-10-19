-- 화면에서 켜고 끄는 운영 설정. 지금은 테스트용 자동 승인(auto_approve) 하나다.
CREATE TABLE settings (
    name       TEXT PRIMARY KEY,
    enabled    BOOLEAN     NOT NULL,
    updated_by TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

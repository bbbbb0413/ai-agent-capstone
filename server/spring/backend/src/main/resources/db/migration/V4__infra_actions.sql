-- v3: 조치를 인프라 조치(파드 재시작, 확장, 롤백)로 바꾸고, 고객사마다 반영 방식을 둔다 (ADR 0001 D1·D3·D4).
ALTER TABLE tenants
    ADD COLUMN change_mode TEXT NOT NULL DEFAULT 'DIRECT',   -- GITOPS | DIRECT
    ADD COLUMN namespace   TEXT,                             -- 고객사 Kubernetes 네임스페이스
    ADD COLUMN gitops_path TEXT;                             -- GITOPS 고객사의 설정 저장소 경로

UPDATE tenants SET change_mode = 'GITOPS', namespace = 'tenant-a', gitops_path = 'gitops/tenants/tenant-a',
                   allowed_actions = ARRAY['restart_pods', 'scale_deployment', 'rollback_deployment', 'notice']
WHERE id = 'tenant-a';
UPDATE tenants SET change_mode = 'DIRECT', namespace = 'tenant-b',
                   allowed_actions = ARRAY['restart_pods', 'notice']
WHERE id = 'tenant-b';

ALTER TABLE tenants ADD CONSTRAINT tenants_gitops_path CHECK (change_mode <> 'GITOPS' OR gitops_path IS NOT NULL);

COMMENT ON COLUMN ai_tasks.status IS 'QUEUED | RUNNING | WAITING_APPROVAL | CHANGE_PENDING | DONE | FAILED';
COMMENT ON COLUMN ai_tasks.pending_action IS '승인 대기 중인 도구·인자, 또는 병합·동기화를 기다리는 변경(PR)';

ALTER TABLE tenants DROP COLUMN app_url;   -- 조치는 demo-app 관리 API가 아니라 Kubernetes API·설정 저장소로 반영한다

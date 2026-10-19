-- 고객사 서비스가 compose에서 kind 클러스터로 옮겨갔다 (ADR 0001 D5).
-- backend는 compose 네트워크에 연결된 kind 노드 이름과 NodePort로 닿는다.
UPDATE tenants SET app_url = 'http://opslens-tenants-control-plane:30081' WHERE id = 'tenant-a';
UPDATE tenants SET app_url = 'http://opslens-tenants-control-plane:30082' WHERE id = 'tenant-b';

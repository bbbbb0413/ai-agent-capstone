-- 서비스 카탈로그 (SPEC-fault-based-actions 4.1). 조치를 고르고 검사할 때 쓰는 시스템 구조를 서비스 단위로 둔다.
-- 구조 사실은 문서(RAG)가 아니라 여기에 둔다. 숫자는 정확해야 하고 backend가 계산·검사에 쓰기 때문이다.

ALTER TABLE services
    ADD COLUMN runtime          TEXT    NOT NULL DEFAULT 'kubernetes',  -- kubernetes | docker | vm | ecs
    ADD COLUMN target_cluster   TEXT,                                  -- 접속할 클러스터·호스트
    ADD COLUMN target_namespace TEXT,                                  -- kubernetes 네임스페이스
    ADD COLUMN stateful         BOOLEAN NOT NULL DEFAULT false,        -- 상태 저장이면 재시작·확장을 하지 않는다
    ADD COLUMN min_replicas     INT     NOT NULL DEFAULT 1,            -- 계약상 하한
    ADD COLUMN max_replicas     INT     NOT NULL DEFAULT 1,            -- 계약상 상한
    ADD COLUMN pool_size        INT;                                   -- DB 커넥션 풀 기준값 (실제 값은 배포 설정에서 읽는다)

-- 의존 대상. 종류와 소유자로 병목 판단과 에스컬레이션 대상을 정하고, DB 최대 연결 수로 확장 안전 상한을 계산한다
CREATE TABLE service_dependencies (
    tenant_id           TEXT          NOT NULL,
    service             TEXT          NOT NULL,
    name                TEXT          NOT NULL,
    kind                TEXT          NOT NULL,          -- database | external_api | internal_service
    owner               TEXT          NOT NULL,          -- tenant | third_party | msp
    owner_contact       TEXT,                            -- 에스컬레이션 대상 (예: A사 DBA팀)
    max_connections     INT,                             -- database만
    connection_headroom NUMERIC(3, 2) NOT NULL DEFAULT 0.80,  -- 관리·마이그레이션용으로 남기는 연결 비율의 나머지
    PRIMARY KEY (tenant_id, service, name),
    FOREIGN KEY (tenant_id, service) REFERENCES services (tenant_id, name)
);

UPDATE services SET target_cluster = 'kind-opslens-tenants', target_namespace = 'tenant-a',
                    min_replicas = 2, max_replicas = 6, pool_size = 20
WHERE tenant_id = 'tenant-a' AND name = 'order-api';
UPDATE services SET target_cluster = 'kind-opslens-tenants', target_namespace = 'tenant-b',
                    min_replicas = 1, max_replicas = 1, pool_size = 20
WHERE tenant_id = 'tenant-b' AND name = 'reservation-api';

INSERT INTO service_dependencies (tenant_id, service, name, kind, owner, owner_contact, max_connections) VALUES
    ('tenant-a', 'order-api',       'postgres',        'database',     'tenant',      'A사 DBA팀',          100),
    ('tenant-b', 'reservation-api', 'payment-gateway', 'external_api', 'third_party', 'B사 결제 담당(대행사 문의)', NULL);

-- B사의 정산 배치는 Docker 단독 호스트에서 돈다. OpsLens가 지원하지 않는 실행 환경의 예다 (guard-runtime 검증)
INSERT INTO services (tenant_id, name, runtime, target_cluster, stateful, min_replicas, max_replicas) VALUES
    ('tenant-b', 'settlement-batch', 'docker', 'b-batch-01', true, 1, 1);

ALTER TABLE services DROP COLUMN depends_on;   -- service_dependencies로 옮겼다

-- 서비스별 관측 정의 (SPEC-limitations-fixes 4단계). agent가 진단·회복 확인 쿼리를 이 값으로 만든다.
-- 쿼리에는 agent가 service="<서비스 이름>" 조건을 붙인다. 고객사 구분은 고객사별 데이터소스가 한다.
-- 관측 정의가 없는 서비스(request_metric이 NULL)는 진단을 원인 미확정으로 끝낸다.

ALTER TABLE services
    ADD COLUMN request_metric TEXT,    -- 요청 히스토그램 이름. _count·_bucket을 붙여 쓴다
    ADD COLUMN error_selector TEXT,    -- 에러 요청을 고르는 라벨 조건
    ADD COLUMN log_selector   TEXT,    -- Loki 라벨 조건 (중괄호 없이)
    ADD COLUMN log_signals    JSONB,   -- 신호 이름 → 로그 문구. 신호 이름은 런북 조항과 묶여 고정이다 (agent signals.LOG_SIGNALS)
    ADD COLUMN pool_size_env  TEXT;    -- 배포 설정에서 DB 커넥션 풀 크기를 읽을 환경변수 이름

UPDATE services SET request_metric = 'demo_orders_seconds',
                    error_selector = 'outcome="error"',
                    log_selector   = 'app="demo-app"',
                    log_signals    = '{"connection_pool_exhausted": "Connection is not available",
                                       "worker_pool_saturated": "503 worker pool saturated",
                                       "request_queue_backlog": "request queue backlog",
                                       "external_api_timeout": "payment API timeout",
                                       "slow_query": "slow query"}',
                    pool_size_env  = 'DEMO_POOL_SIZE'
WHERE (tenant_id, name) IN (('tenant-a', 'order-api'), ('tenant-b', 'reservation-api'));

-- 장애용 데모 서비스를 고객사마다 더한다 (SPEC-e2e-speed 8.1). 서비스가 다르면 같은 고객사 안에서도 E2E 시나리오를
-- 동시에 돌 수 있다. 모두 같은 데모 앱 이미지라 관측 정의와 의존 대상은 기존 서비스와 같고 이름만 다르다.
--   A사 cart-api       HPA 2~3, 안전 상한 4 (order-api와 같은 구조)
--   A사 inventory-api  레플리카 2 고정 (HPA 없음)
--   B사 checkin-api    레플리카 1 (reservation-api와 같은 구조)

INSERT INTO services (tenant_id, name, runtime, target_cluster, target_namespace, stateful, min_replicas, max_replicas,
                      pool_size, request_metric, error_selector, log_selector, log_signals, pool_size_env)
SELECT tenant_id, new_name, runtime, target_cluster, target_namespace, stateful, min_replicas, max_replicas,
       pool_size, request_metric, error_selector, log_selector, log_signals, pool_size_env
FROM services
JOIN (VALUES ('tenant-a', 'order-api', 'cart-api'),
             ('tenant-a', 'order-api', 'inventory-api'),
             ('tenant-b', 'reservation-api', 'checkin-api')) AS copy (t, source, new_name)
  ON services.tenant_id = copy.t AND services.name = copy.source
ON CONFLICT DO NOTHING;

INSERT INTO service_dependencies (tenant_id, service, name, kind, owner, owner_contact, max_connections,
                                  connection_headroom)
SELECT d.tenant_id, copy.new_name, d.name, d.kind, d.owner, d.owner_contact, d.max_connections, d.connection_headroom
FROM service_dependencies d
JOIN (VALUES ('tenant-a', 'order-api', 'cart-api'),
             ('tenant-a', 'order-api', 'inventory-api'),
             ('tenant-b', 'reservation-api', 'checkin-api')) AS copy (t, source, new_name)
  ON d.tenant_id = copy.t AND d.service = copy.source
ON CONFLICT DO NOTHING;

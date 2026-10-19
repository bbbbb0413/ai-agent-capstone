-- 데모 고객사와 계정. 비밀번호는 초안용 평문({noop})이며 발표 전 bcrypt로 바꾼다.

INSERT INTO tenants (id, name, sla_tier, availability_target, response_minutes, app_url, allowed_actions) VALUES
    ('tenant-a', 'A사 온라인 쇼핑몰', 'GOLD',   99.90, 15, 'http://demo-app-a:8080',
     ARRAY['adjust_pool', 'restart', 'clear_fault', 'notice']),
    ('tenant-b', 'B사 예약 플랫폼',   'SILVER', 99.50, 60, 'http://demo-app-b:8080',
     ARRAY['clear_fault', 'notice']);

INSERT INTO services (tenant_id, name, depends_on) VALUES
    ('tenant-a', 'order-api',       ARRAY['postgres']),
    ('tenant-b', 'reservation-api', ARRAY['payment-gateway']);

INSERT INTO users (username, password, role, tenant_id) VALUES
    ('admin',    '{noop}admin1234',    'MSP_ADMIN',     NULL),
    ('engineer', '{noop}engineer1234', 'MSP_ENGINEER',  NULL),
    ('viewer-a', '{noop}viewer1234',   'TENANT_VIEWER', 'tenant-a'),
    ('viewer-b', '{noop}viewer1234',   'TENANT_VIEWER', 'tenant-b');

INSERT INTO user_tenants (user_id, tenant_id)
SELECT id, 'tenant-a' FROM users WHERE username = 'engineer'
UNION ALL
SELECT id, 'tenant-b' FROM users WHERE username = 'engineer';

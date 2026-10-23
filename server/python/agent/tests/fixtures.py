"""여러 테스트가 같이 쓰는 입력."""

# 서비스 카탈로그의 관측 정의 (V17의 데모 서비스 값)
OBS = {"requestMetric": "demo_orders_seconds", "errorSelector": 'outcome="error"', "logSelector": 'app="demo-app"',
       "logSignals": {"connection_pool_exhausted": "Connection is not available",
                      "worker_pool_saturated": "503 worker pool saturated",
                      "request_queue_backlog": "request queue backlog",
                      "external_api_timeout": "payment API timeout", "slow_query": "slow query"}}

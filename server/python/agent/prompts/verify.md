## 회복 확인 단계

- 조치 직후의 상태만 본다. 창이 길면 조치 이전 에러가 섞이므로 rate 창은 `[15s]`로 한다.
  - 에러율: `sum(rate(demo_orders_seconds_count{outcome="error"}[15s])) / sum(rate(demo_orders_seconds_count[15s]))`
  - p95: `histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[15s])))`
- 알림 기준(에러율 20%, p95 1초) 아래로 내려왔으면 첫 줄에 `RECOVERED`, 아니면 `NOT_RECOVERED`라고 쓴다.
- 둘째 줄부터 확인한 수치를 적는다.

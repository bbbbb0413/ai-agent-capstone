## 회복 확인 단계

- 조치 직후의 상태만 본다. 창이 길면 조치 이전 에러가 섞이므로 rate 창은 `[15s]`로 한다.
  - 에러율: `(sum(rate(demo_orders_seconds_count{outcome="error"}[15s])) or vector(0)) / sum(rate(demo_orders_seconds_count[15s]))`
  - p95: `histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[15s])))`
- 에러가 하나도 없으면 에러 시계열 자체가 없다. 그래서 에러율 쿼리에 `or vector(0)`을 붙인다. 결과가 0이면 에러가 없는 것이다.
- 전체 요청 수(`sum(rate(demo_orders_seconds_count[15s]))`)까지 비어 있으면 아직 새 파드의 메트릭이 수집되지 않은 것이다. 이때는 회복 여부를 판단하지 말고 같은 쿼리를 한 번 더 조회한다.
- 알림 기준(에러율 20%, p95 1초) 아래로 내려왔으면 첫 줄에 `RECOVERED`, 아니면 `NOT_RECOVERED`라고 쓴다.
- 둘째 줄부터 확인한 수치를 적는다.

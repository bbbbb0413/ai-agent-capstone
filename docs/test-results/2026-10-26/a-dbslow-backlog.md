# A-dbslow+surge. DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (A사)

- 결과: **통과** (84초)
- 작업: http://127.0.0.1:18000/tasks/7f934b32-eef3-411e-862f-8d995580b2a7
- 진행 기록: [a-dbslow-backlog.log](a-dbslow-backlog.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-05, 실제 RB-STD-05)
- ✅ 원인 후보에 RB-STD-05가 있다 (적힌 후보: RB-STD-03, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_escalate_incident cart-api DEPENDENCY postgres)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (앱 조치 없음)
- ✅ 에스컬레이션 DEPENDENCY postgres 대기열 등록 (있음: DEPENDENCY postgres)
- ✅ 받는 사람에 A사 DBA팀이 있다 (engineer, admin, A사 DBA팀)
- ✅ 인시던트가 하나만 열렸다 (#14)

## 참고

- DB p95 / 요청 p95 = 0.64 → 대기열은 DB 지연의 증상
- 인시던트 #14에 묶인 알림 1개, 작업에 남은 알림 추가 0건

## Agent 보고서

## 요약
tenant-a cart-api에서 p95 응답시간이 1초를 초과하는 장애가 발생했고, 현재는 의존 대상인 Postgres의 상태가 DEGRADED로 확인되어 지연이 지속 중입니다. 주 원인 후보로 DB 지연(RB-STD-05)과 트래픽 증가로 인한 대기열 증가(RB-STD-03)가 제시되었습니다. 이번 문제는 의존 대상(DEGRADE된 Postgres) 소유 팀과의 협업이 필요합니다.

## 근거
- 메트릭: Prometheus 확인 결과 p95_seconds(cart-api) 2.48초, error_rate 0.0, requests_per_second 약 73.16
- 로그: Grafana Loki 로그 요약에서 slow query 다수 발생 및 60건의 request_queue_backlog 경고
  - 예시: slow query: SELECT * FROM orders WHERE customer_id = ? FOR UPDATE took 1194ms (db=postgres)
  - backlog: 60 requests waiting for a processing slot (4/4 slots busy), queue wait 776ms
- 변경 이력: cart-api.yaml에서 DEMO_POOL_SIZE를 20에서 5로 변경했다가 원복 중(최근 변경은 20으로 재설정)
- 문서: runbooks 및 SLA 관련 문서 참고
- 과거 유사 사례: postmortem-incident-5(장애 #5: cart-api p95 응답시간 1초 초과) 관련 기록 참조
- 의존 상태: postgres DEPENDENCY가 DEGRADED 상태로 표시, p95Seconds 1.591초(최근 1분 창)
- 확인 로그/메시지: “slow query” 로그 집중, “request_queue_backlog” 확인

## 원인 후보
1) RB-STD-05 DB 지연 — p95DB 대기시간 증가, Postgres 상태 DEGRADED, slow_query 다수 (처리 근거: evidence의 slow_query 다수 및 DEGRADED 상태)
2) RB-STD-03 트래픽 급증 — request_queue_backlog 59건(증상: RB-STD-03) (처리 근거: evidence의 backlog 59건)
3) RB-STD-04 외부 의존 지연 — 외부 API timeout 여부 없음(근거 없음)
- 최종 판단 후보: RB-STD-05를 주 원인으로 보되 RB-STD-03의 대기열 증상 역시 병목에 기여

## 조치와 결과
- 승인자: 엔지니어
- 실행 내용: 에스컬레이션 등록 및 의존 대상 소유자(A사 DBA팀)와의 협업 필요성 공지
- 실행 방식: SLA 규정에 따라 PR 반영 및 필요 시 롤백 절차 예정(상세 실행은 DBA 협업 종료 후 결정)
- 회복 확인 수치: 미확정(N/A)

승인 상태: APPROVE (e2e)로 승인되었으며, 에스컬레이션이 QUEUED 상태로 진행 중
- 에스컬레이션 ID: 12
- 수신: MSP_ONCALL 엔지니어,

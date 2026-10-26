# A-dbslow+surge. DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (A사)

- 결과: **통과** (95초)
- 작업: http://localhost:18000/tasks/d6700c6f-ec20-4b0c-8018-23c65e8fbf8f
- 진행 기록: [a-dbslow-backlog.log](a-dbslow-backlog.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-05, 실제 RB-STD-05)
- ✅ 원인 후보에 RB-STD-05가 있다 (적힌 후보: RB-STD-01, RB-STD-02, RB-STD-03, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_escalate_incident DEPENDENCY postgres)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (앱 조치 없음)
- ✅ 에스컬레이션 DEPENDENCY postgres 대기열 등록 (있음: DEPENDENCY postgres)
- ✅ 받는 사람에 A사 DBA팀이 있다 (engineer, admin, A사 DBA팀)
- ✅ 인시던트가 하나만 열렸다 (#5)

## 참고

- DB p95 / 요청 p95 = 0.65 → 대기열은 DB 지연의 증상
- 인시던트 #5에 묶인 알림 1개, 작업에 남은 알림 추가 0건

## Agent 보고서 (실행 당시 생성문, 검증된 사실과 구분)

실행기가 보관한 앞부분(최대 1,500자)이다. 판정은 위 검사 결과와 이벤트·감사 기록을 따른다. 보고서의 수치·단위·서술 정확도 전체를 검증한 결과가 아니다.

## 요약
tenant-a cart-api의 p95 응답시간이 1초대를 초과하는 장애가 발생했고, DB 의 Slow Query로 인한 지연이 주된 원인으로 확인되었습니다. 현재는 의존 대상(Postgres)의 상태가 DEGRADED로 확인되어 Slow Query가 지속되고 있으며, DB 쿼리 대기열(backlog) 증가가 관측됩니다. 에스컬레이션이 QUEUED 상태이며, 고객사 DBA팀과의 협업이 필요합니다.

## 근거
- 메트릭: 최근 1분 p95Seconds 1.611초, callsPerSecond 약 111.04, 종합 p95 1.611s (postgres 의 DEGRADED 상태)
- 로그: slow_query 로그 다수 — 예시: "slow query: SELECT * FROM orders WHERE customer_id = ? FOR UPDATE took 1452ms (db=postgres)" (WARN 레벨)
- 변경 이력: 직전 변경 시점에 장애용 데모 서비스 추가 및 fault 주입 구성 반영 (2026-10-23T05:47:44Z)
- 문서: MSP 표준 장애 대응 런북 RB-STD-05, RB-STD-03, RB-STD-01 참고
- 증거 종합: 의존 대상(Postgres)의 DEGRADED 상태로 인해 slow_query가 지속되고, request_queue_backlog 52건으로 확인
- 증거 도구 요약
  - grafana_query_loki_logs: slow query 다수 로그 확인
  - grafana_query_prometheus: p95Seconds 1.611s, requests_per_second 약 91–111대
  - business_get_dependency_health: postgres 의 상태 DEGRADED, p95Seconds 1.611, callsPerSecond 111.037
  - 함께 제공된 변경 이력/런북: RB-STD-05(의존 대상), RB-STD-03(트래픽 급증 대응), RB-A-02/RB-A-03(고객 공지/DB 관련 주의사항)

## 원인 후보
1) RB-STD-05 slow_query — 5686건, dependency=postgres, dependency_status=DEGRADED, dependency_share=0.65 (증상: RB-STD-03)
   - first_seen: 2026-10-26T00:22:55+00:00
   - evidence: true
2) RB-STD-03 요청 대기열(backlog) — signal_count=52, first_seen=2026-10-26T00:23:00+00:00
   - signal: request_queue_backlog
   - evidence: true
3) RB-STD-01 연결 풀 고갈 — signal_count=0, first_seen=null
   - evidence: false
4) RB-STD-02 작업 스레드 포화 — signal_count=0, first_seen=null
   - eviden

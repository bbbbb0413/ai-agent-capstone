# B-pool. 잘못된 배포 → 롤백 불가 에스컬레이션 (B사)

- 결과: **통과** (102초)
- 작업: http://127.0.0.1:18000/tasks/e652faba-3755-4964-8a8b-bfaa85e8aa82
- 진행 기록: [b-pool.log](b-pool.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_escalate_incident reservation-api ACTION_BLOCKED rollback_deployment CONTRACT)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 에스컬레이션 ACTION_BLOCKED rollback_deployment CONTRACT 대기열 등록 (있음: P1, ACTION_BLOCKED CONTRACT rollback_deployment)
- ✅ 받는 사람에 B사 운영 승인 담당이 있다 (engineer, admin, B사 운영 승인 담당)
- ✅ 고객 공지가 발송됐다
- ✅ 롤백이 계약상 불가라 앱 조치를 실행하지 않았다 (0건)

## Agent 보고서

## 요약
 tenant-b reservation-api 에러율이 20%를 초과하는 장애가 발생했고, 현재 정상화되지는 않았습니다. 주요 원인 후보로 RB-STD-01의 연결 풀 고갈이 관측되며, 직전 배포 시점과의 시차를 고려해 배포 영향 가능성을 확인 중입니다. 지금은 실행된 조치가 없고, 고객 승인 후 롤백 등 조치를 준비 중입니다.

## 근거
- 메트릭: 에러율 0.3545, p95 0.128초, 최근 1분 요청 수 5.16/s
- 로그: 503 Service Unavailable 다수, HikariPool - Connection is not available, request timed out 다수
- 변경 이력: reservation-api 배포 이력 revision 4 (image OPSLens/demo-app:0.1.0), deployedAt 2026-10-26T01:13:29Z
- 문서: MSP 표준 런북 RB-STD-01 설정 변경으로 인한 DB 커넥션 풀 고갈, RB-STD-02 재시작 주의 등
- 실행 이력: escalation 및 승인 대기 상태, restart_pods 등은 미실행 상태
- 참고: 과거 장애 #2와의 유사 이력 있음(참고 목적, 원인 확정 아님)

## 원인 후보
1) RB-STD-01 — connection_pool_exhausted — 114건 신호 (first_seen 2026-10-26T01:13:25+00:00) (근거 있음) · 처리: 후보에서 우선 원인으로 두고 조치 계획
2) RB-STD-04 — external_api_timeout(의존: payment-gateway, 상태 DEGRADED, dependency_share 18.54) — 근거 있음
3) RB-STD-02 — worker_pool_saturated — 신호 0 (근거 없음)
4) RB-STD-03 — request_queue_backlog — 신호 0 (근거 없음)

- 처리할 원인: RB-STD-01
- 병목: 앱

## 조치와 결과
- 승인 상태: 승인 필요(승인자 결정 대기). 승인 여부에 따라 조치를 실행 여부 판단
- 현재 실행된 조치: 없음
- 실행 방식: 승인 후 실행 예정. (직접 실행 여부는 승인 여부에 달려 있음)
- 제안된 조치(승인 요청용 요약):
  - RB-STD-01에 따라 앱 내 연결 풀 관리 점검 및 파드 재시작 여부를 고려
  - 필요 시 payment-gateway 의존 지연 모니터링 및 외부 연락 창구 확인 병행
- 참고: RB-STD-01에 따른 롤백 조치가 필요하나 계약상 롤백은 고객 승인 필요로 즉시 실행 불가

- 에스컬레이션 및 승인의 근거: RB-STD-01의 롤백 및 재배포 관련 프로토콜 및 SLA 제3조 허용 조치에 따라 고객 승인 필요

## SLA 영향
- 도구로 받은 응답 기한 및 남은 시간은 아래와 같음
  - remainingMinutes: 59
  - responseDeadline: 2026-10-26T02:14:07.053467Z

## 권장 다음 행동


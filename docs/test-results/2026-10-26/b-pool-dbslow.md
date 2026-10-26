# B-pool+dbslow. 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (B사)

- 결과: **통과** (142초)
- 작업: http://127.0.0.1:18000/tasks/83b19373-5f1e-4982-9eaf-2e1cc5dd1552
- 진행 기록: [b-pool-dbslow.log](b-pool-dbslow.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-01, 실제 RB-STD-01)
- ✅ 원인 후보에 RB-STD-01, RB-STD-05가 있다 (적힌 후보: RB-STD-01, RB-STD-02, RB-STD-03, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_escalate_incident reservation-api ACTION_BLOCKED rollback_deployment CONTRACT)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (앱 조치 없음)
- ✅ 에스컬레이션 ACTION_BLOCKED rollback_deployment CONTRACT 대기열 등록 (있음: ACTION_BLOCKED CONTRACT rollback_deployment)
- ✅ 받는 사람에 B사 운영 승인 담당이 있다 (engineer, admin, B사 운영 승인 담당)
- ✅ 롤백이 계약상 불가라 앱 조치를 실행하지 않았다 (0건)
- ✅ 인시던트가 하나만 열렸다 (#18)

## 참고

- 인시던트 #18에 묶인 알림 2개, 작업에 남은 알림 추가 1건

## Agent 보고서

## 요약
tenant-b의 reservation-api에서 p95 응답시간이 1초를 초과하는 장애가 발생했고 현재 에스컬레이션 상태로 남아 있습니다. RB-STD-01의 우선 원인으로 확인되어 롤백 등 조치가 필요하나 계약상 허용 범위로 인한 실행 차단으로 에스컬레이션이 진행 중입니다.

## 근거
- 메트릭: p95(1.2178초) 및 에러율(0.126) — Prometheus/Prometheus-계 수치, 1분 간격 집계
- 로그: reservation-api에서 다수의 503/연결 풀 고갈 관련 WARN/ERROR
  예시 로그: "HikariPool-1 - Connection is not available, request timed out"
- 변경 이력: 직전 배포 시점 2026-10-26T00:25Z 직후 배포 기록
- 로그 신호: connection_pool_exhausted(37건), slow_query(32건)
- 원인 후보 및 근거: RB-STD-01(커넥션 풀 고갈) 신호 다수, p95 상승, 503 로그 다수
- 외부 의존 건강: payment-gateway HEALTHY(p95 0.059s, 호출 5/s) — 현재 지연 원인에서 배제가 가능
- 조치 상태: RB-STD-01 원인 해결 필요하나 계약상 롤백은 허용 목록에 포함되지 않아 에스컬레이션 필요
- 참고: 과거 장애(#4) 근거 및 런북 문서들을 참조

## 원인 후보
1) RB-STD-01 RB-STD-01 커넥션 풀 고갈 — 신호 37건, first_seen 2026-10-26T01:18:45+00:00, 증거: true · 처리: 우선 원인으로 확정되었으며 조치 필요 (가장 강력한 후보)

다른 후보도 언급되었으나 현재 증거 우선순위는 RB-STD-01에 있음:
- RB-STD-05 DB 지연 — slow_query 32건, first_seen 2026-10-26T01:18:40+00:00, 증거: true
- RB-STD-04 외부 의존 지연 — external_api_timeout 0건, 의존성 건강은 양호
- RB-STD-02 작업 스레드 고갈 — 증거 없음
- RB-STD-03 트래픽 백로그 — 증거 없음

## 조치와 결과
- 승인자: 엔지니어(APPROVE), 승인 ID 300b1b31-509e-59ce-b9db-643cb3935d6e
- 실행 여부: EXECUTED 여부 판단 상태는 없음(에스컬레이션 QUEUED 상태)
- 취해야 할 조치: rollback_deployment(배포 롤백) 필요 but 계약상 허용 목록 미포함으로 실행 차단. 에스컬레이션으로 승인 요청 중
- 조치 근거: RB-STD-01의 우선 원인 확정·지표 신호 다수, 직전 배포 시점의 변경 이력
- 에스컬레이션: 인시던트 #18 에스컬레이션 기록 QUEUED, 수신자(msP_oncall 엔지니어, admin, 고객 운영 승인 담당)

- 변경 이력
  - 현재 상태: change 없음
  - 직전 배포: revision 6, 이미지 opslens/demo-app:0.

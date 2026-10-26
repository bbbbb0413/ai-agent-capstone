# A-pool. 잘못된 설정 배포 → 롤백 PR (A사, GITOPS)

- 결과: **통과** (231초)
- 작업: http://127.0.0.1:18000/tasks/1f944ddf-43ed-4a94-b6a5-a2eba9059fc0
- 진행 기록: [a-pool.log](a-pool.log)

## 판정

- ✅ PR #95이 A사 설정 경로만 바꾼다 (gitops/tenants/tenant-a/cart-api.yaml)
- ✅ PR #95 병합 (Pull Request successfully merged)
- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_execute_runbook_action rollback_deployment cart-api)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ PR 병합 후 ArgoCD 동기화 알림으로 재개됐다
- ✅ 회복 확인: RECOVERED
- ✅ 클러스터의 풀 크기가 20으로 돌아왔다 (현재 20)

## 참고

- 롤백 PR: https://github.com/bbbbb0413/ai-agent-capstone/pull/95

## Agent 보고서

## 요약
cart-api의 에러율이 20%를 초과하지는 않지만, 로그에서 커넥션 풀 고갈이 주된 원인 신호로 확인되었습니다. 직전 변경으로 커넥션 풀 크기가 5로 축소되었고 이로 인해 동시 요청 처리 여유가 감소하여 풀 고갈이 발생한 것으로 보이며, 롤백 조치를 통해 정상화되었습니다.

## 근거
- 메트릭: Prometheus 합계 지표에서 에러율 0.292, p95 0.13초, 최근 1분 지표(요청 수 10.64/s)
- 로그: Connection is not available, request timed out after 30000ms; order failed: 503 Service Unavailable
- 로그/신호: cart-api 로그에서 connection_pool_exhausted 다수 발생(예: 184건 신호 발생 직전 시점)
- 변경 이력: 직전 변경 2026-10-26T01:10:31Z에서 DEMO_POOL_SIZE를 20에서 5로 축소
- 문서: RB-STD-01 설정 변경으로 인한 DB 커넥션 풀 고갈; RB-STD-03 트래픽 급증(앱 처리 용량 부족)
- 과거 이력: past_incidents에 장애 #5 관련 기록 존재(Cart-api p95 응답시간 이슈 및 DB 상태 DEGRADED)

## 원인 후보
1. RB-STD-01 커넥션 풀 고갈 — 184건의 connection_pool_exhausted 신호, first_seen 2026-10-26T01:11:05+00:00 (증상: 커넥션 풀 고갈) (처리 순서: 최우선 후보)
2. RB-STD-05 DB 지연(있지만 근거 약함) — 의존 대상 postgres HEALTHY, p95 0.009초, 신호 0건 (근거 부족)
3. RB-STD-02 worker_pool_saturated/RB-STD-03 request_queue_backlog — 신호 0건으로 근거 부족

## 조치와 결과
- 승인자: 엔지니어(승인 ID a3a37fe0-78db-5ef8-b5a8-44050fde0fea)
- 실행 내용: cart-api 롤백 배포 롤백(rollback_deployment)
- 실행 방식: 변경 이력 PR 반영 및 레포지토리 PR #95 병합 후 ArgoCD 동기화 대기
- 회복 확인 수치: 에러율 0.00, p95 0.14초
- 회복 여부: recovered = true
- 근거 수치: 롤백 전 신호 RB-STD-01의 connection_pool_exhausted 184건 지속
- 처리된 원인: RB-STD-01
- 해결 시나리오: PR #95 병합 및 동기화 완료 후 서비스 정상화 확인

- 조치 후 추가 확인: 에러율 0.00(목표 0.20 이하), p95 0.14초(목표 1초 이하); 그 외 신호 변화도 확인됨

## SLA 영향
- 도구가 준 응답 기한과 남은 시간 값을 그대로 반영
- 남은 SLA 기준 시점 및 남은 시간: 14분 남음(remainingMinutes: 14)으로 계산되어 표기

## 권장 다음 행동
- 원인 RB-STD-01 재발 방

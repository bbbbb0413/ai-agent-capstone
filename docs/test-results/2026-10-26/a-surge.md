# A-surge. 트래픽 급증 → HPA 최대치 4 PR (A사, GITOPS)

- 결과: **통과** (164초)
- 작업: http://127.0.0.1:18000/tasks/4abf9ab1-27df-401b-bfe4-ce393c88164a
- 진행 기록: [a-surge.log](a-surge.log)

## 판정

- ✅ PR #94이 A사 설정 경로만 바꾼다 (gitops/tenants/tenant-a/hpa.yaml)
- ✅ PR #94 병합 (Pull Request successfully merged)
- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_execute_runbook_action adjust_autoscaler order-api 4)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 안전 상한 4을 넘는 최대치를 제안하지 않았다
- ✅ PR 병합·동기화 후 HPA 최대치가 4다 (현재 4)
- ✅ 회복 확인: RECOVERED

## 참고

- 롤백 PR: https://github.com/bbbbb0413/ai-agent-capstone/pull/94

## Agent 보고서

## 요약
tenant-a의 order-api p95 응답시간 초과 장애가 발생했으나, 백로그 신호를 바탕으로 RB-STD-03에 따라 앱 측 autoscaler를 조정한 결과 정상화되었습니다. 현재 지표는 회복 상태이며, 백로그 초기 신호는 해소되었습니다.

## 근거
- 메트릭: p95Seconds 2.13s → 조치 직후 0.17s로 회복(기준 1.0s)
- 로그: request_queue_backlog 67건(첫 관측 2026-10-26T01:10:30Z) 및 WARN 로그 예시 다수
- 변경 이력: 직전 변경 시점에 order-api 관련 의존 장애 구성 반영(GitOps 저장소 변경)
- 문서: MSP 표준 장애 대응 런북 RB-STD-03, RB-STD-01, RB-STD-05 참고
- 인시던트: order-api(Open) 상태, 백로그 지속 신호 포함

## 원인 후보
1) RB-STD-03 트래픽 급증 — 67건의 request_queue_backlog 신호(first_seen 2026-10-26T01:10:30) · 증상: 백로그로 인한 순차 처리 지연
2) RB-STD-01 풀 고갈 — connection_pool_exhausted 신호 없음
3) RB-STD-02 작업 스레드 고갈 — worker_pool_saturated 신호 없음
4) RB-STD-04 외부 의존 지연 — external_api_timeout 신호 없음
5) RB-STD-05 DB 지연 — slow_query 신호 없음(Postgres HEALTHY, dependency_share 0.0)
- 최종 판단: RB-STD-03이 현재 장애의 주된 원인 후보로 판단

## 조치와 결과
- 승인자: 엔지니어 승인 완료(APPROVE, ID: 471252ea-f968-585f-a8ac-e7d088ee879c)
- 실행 내용: autoscaler 조정(주요 서비스 order-api) 및 PR 생성 후 GitOps 반영
- 실행 방식: 자동 확장 조정 via PR 반영(GITOPS); PR #94 병합 및 ArgoCD 동기화 완료
- 회복 확인 수치: 에러율 0.00, p95Seconds 0.17초로 확인
- 요약: RB-STD-03에 따른 조치로 백로그 축적이 해소되며 정상화 확인

## SLA 영향
- 도구가 준 응답 기한 및 남은 시간 값을 그대로 인용: 남은 시간 및 기한은 도구 출력에 따른 값 사용

## 권장 다음 행동
- 현재 조치로 회복되었으나, 트래픽 급증 가능성 지속 시 모니터링 강화 및 필요 시 자동 스케일링 파라미터 재점검 권고
- 향후 재발 방지를 위해 유사 상황 시 조치 루프를 자동화하는 추가 시나리오 검토

## 조사 과정
- grafana_query_loki_logs: order-api의 WARN 로그 및 backlog 신호 확인 (첫 백로그 67건, 예시 로그: “request queue backlog: 166 requests waiting for a processing slot (4/4 slots busy), queue wa

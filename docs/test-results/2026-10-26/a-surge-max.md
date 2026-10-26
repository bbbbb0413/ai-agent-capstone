# A-surge-max. 안전 상한으로도 부족 → 에스컬레이션 (A사)

- 결과: **통과** (190초)
- 작업: http://127.0.0.1:18000/tasks/02f96685-1bfd-4fb6-8e7c-f00ce1fc4d93
- 진행 기록: [a-surge-max.log](a-surge-max.log)

## 판정

- ✅ PR #96이 A사 설정 경로만 바꾼다 (gitops/tenants/tenant-a/hpa.yaml)
- ✅ PR #96 병합 (Pull Request successfully merged)
- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_execute_runbook_action adjust_autoscaler order-api 4)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 안전 상한 4을 넘는 최대치를 제안하지 않았다
- ✅ 최대치 4로도 회복되지 않았다고 판정했다
- ✅ 에스컬레이션 ACTION_BLOCKED STRUCTURE 대기열 등록 (있음: ACTION_BLOCKED STRUCTURE adjust_autoscaler)
- ✅ 받는 사람에 A사 운영 담당이 있다 (engineer, admin, A사 운영 담당)
- ✅ 고객 공지가 발송됐다

## 참고

- 롤백 PR: https://github.com/bbbbb0413/ai-agent-capstone/pull/96

## Agent 보고서

## 요약
tenant-a의 order-api p95 응답시간이 1초를 초과하는 장애가 지속 중이며, 현재 백로그가 누적되어 처리 지연이 발생하고 있습니다. DB는 HEALTHY하지만 앱 내부의 처리 대기 및 백로그가 주된 원인으로 보이며, RB-STD-03 대응을 위한 자동 확장 조치가 최대치에 도달해 추가 확장이 어려운 상황입니다. 다음 조치로 autoscaler 조정 PR이 생성되었고 에스컬레이션이 대기 중이며, 추가 모니터링 및 조치가 필요합니다.

## 근거
- 메트릭: p95_seconds 5.32초 (최근 1분 창), p95_seconds 5.65초(Prometheus 1m 창), RPS 238.48, error_rate 0.0
- 로그: WARN 로그에서 order-api의 request queue backlog가 지속적으로 증가하고 있음(예시: 316→211→306 등). latest backlog: 210건, queue wait 2587ms
- 변경 이력: 2026-10-23T05:47:44Z에 order-api autoscaler 조정 및 로그/의존 장애 구성 변경
- 문서: MSP 표준 런북 RB-STD-03 트래픽 급증, RB-A-02 DB 관련 연락 및 주의, RB-A-03 고객 공지 기준
- 의존 대상 상태: Postgres HEALTHY (p95Seconds 0.009, callsPerSecond 27.008)
- 원인 후보 및 최근 판단 근거: RB-STD-03 신호(signal_count 210, first_seen 2026-10-26T01:15:45Z) 가장 강력한 증상; RB-STD-07은 최종 처리 대상 대기로 남김
- 실행 상태: autoscaler 조정 PR 생성 완료(병합 대기 중), 에스컬레이션 QUEUED 상태

## 원인 후보
1) RB-STD-03 트래픽 급증 — signal_count: 210, first_seen: 2026-10-26T01:15:45+00:00 · 증상: 백로그로 인한 순차 처리 지연 (근거: evidence=true)
2) RB-STD-01 풀 고갈 — signal: connection_pool_exhausted, signal_count: 0 (근거 없음)
3) RB-STD-02 작업 스레드 고갈 — signal: worker_pool_saturated, signal_count: 0 (근거 없음)
4) RB-STD-04 외부 의존 지연 — signal: external_api_timeout, signal_count: 0 (근거 없음)
5) RB-STD-05 DB 지연 — signal: slow_query, dependency: postgres, dependency_status: HEALTHY (근거 없음)

- 의존 상태: postgres HEALTHY, p95Seconds 0.009, callsPerSecond ~27, 최근 1분 window
- 진단에 따른 처리할 원인: RB-STD-07 원인 미확정; 현재는 앱 내부의 백로그가 핵심 증상으로 남음

## 조치와 

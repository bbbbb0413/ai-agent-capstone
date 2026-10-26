# B-hang. 파드 멈춤 → 재시작 (B사, DIRECT)

- 결과: **통과** (173초)
- 작업: http://127.0.0.1:18000/tasks/8278bbbb-7a57-4f8d-8f53-35496194a62f
- 진행 기록: [b-hang.log](b-hang.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_execute_runbook_action restart_pods reservation-api)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 승인 카드가 재시작 중 전체 서비스 중단을 알린다 (레플리카 1)
- ✅ 재시작이 Kubernetes API로 실행됐다
- ✅ 회복 확인: RECOVERED
- ✅ 파드가 새로 만들어졌다

## Agent 보고서

## 요약
 tenant-b의 reservation-api에서 에러율이 20%를 초과하는 장애가 발생했다가 재시작 조치로 회복되었다. 현재 에러율은 0.00%, p95는 0.14초로 정상화되었다.

## 근거
- 메트릭: 에러율 1.0으로 상승하다 회복 수치 0.00%(기준 0.20%), p95 0.14초(기준 1초)로 확인
- 로그: 503 worker pool saturated, oldest worker blocked 관련 메시지 다수
  예시 로그: “order rejected: 503 worker pool saturated (active=200/200, queued=1000/1000), oldest worker blocked 245s waiting for internal lock ReservationCache.refreshLock (deadlock suspected)”
- 의존 상태: payment-gateway 상태_DEGRADED, dependency_share 25.06
- 변경 이력: reservation-api 배포 이력 opSlens/demo-app:0.1.0, deployedAt 2026-10-23T07:34:28Z
- 실행 이력: restart_pods(reservation-api) — 단일 파드 재시작(ALL_AT_ONCE)
- 회복 확인: 에러율 0.00, p95 0.14초
- 런북 참조: RB-STD-02 작업 스레드 고갈, RB-STD-03 트래픽 급증, RB-B-02 재시작 주의
- 과거 장애: 장애 #2와 동일한 서비스에서의 유사 이력 존재(참고 목적, 현재 원인 확정 아님)
- 실행 승인: APPROVE(두 명의 엔지니어 승인)

## 원인 후보
1) RB-STD-02 — worker_pool_saturated — 363건 신호( first_seen 2026-10-26T01:10:30+00:00 ) (증상: RB-STD-02) → 처리 원인으로 확인하고 조치 수행. (처리 순서: 1)
2) RB-STD-04 — external_api_timeout(의존: payment-gateway, 상태_DEGRADED, dependency_share 25.06) → 의존 상태를 고려한 보조 원인 후보. (처리 순서: 2)
3) RB-STD-03 — request_queue_backlog(신호_count 0) → 증상으로 확인되지 않음. (처리 순서: 3)

조치 결정: RB-STD-02를 주된 원인 후보로 판단하고 조치 실행.

## 조치와 결과
- 승인자: 엔지니어 (APPROVE 및 실행 승인ID 포함)
- 실행 내용: restart_pods, reservation-api, runtime Kubernetes, DIRECT 방식으로 ALL_AT_ONCE로 재시작
- 반영 방식: 실행 즉시 수행
- 회복 확인 수치: 에러율 0.00, p95 0.14초
- 조치 결과: EXECUTED (reservation-api 파드 재시작 완료)
- 에스컬레이션: QUEUED 상태로 Slack 전달 예정(별도 검토 필요)

참고:

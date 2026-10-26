# A-hang. 파드 멈춤 → 순차 재시작 (A사, GITOPS)

- 결과: **통과** (162초)
- 작업: http://127.0.0.1:18000/tasks/72522467-2915-4276-abbd-753eeffaea6f
- 진행 기록: [a-hang.log](a-hang.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_execute_runbook_action restart_pods inventory-api)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 승인 카드가 순차 재시작임을 알린다
- ✅ 승인 카드가 전체 서비스 중단 없음을 알린다
- ✅ 승인 카드에 현재 레플리카가 표시된다
- ✅ 재시작이 Kubernetes API로 실행됐다
- ✅ 실제 조치도 파드를 하나씩 재시작했다
- ✅ 회복 확인: RECOVERED
- ✅ 파드가 새로 만들어졌다

## Agent 보고서

## 요약
tenant-a inventory-api의 에러율이 20%를 초과하였으나 조치 후 정상화되었습니다. 현재 에러율 0%, p95 응답시간 0.1316초로 확인되었습니다.

## 근거
- 메트릭: 에러율 0.543541386992818 (ping: 1m 단위), p95Seconds 0.1253789726651331초, 요청 수(RPS) 9.943370013399472
- 로그: 100건의 ERROR 예시 중 하나로 “ERROR [scheduling-1] c.o.d.OrderService - order rejected: 503 worker pool saturated (active=200/200, queued=1000/1000)”
- 변경 이력: 직전 변경 시점에 장애용 데모 서비스 추가 및 fault 주입 구성 반영(CI/CD gitops 변화) (예: 2026-10-23T05:47:44Z)
- 문서: MSP 표준 장애 대응 런북 RB-STD-02, RB-STD-03, RB-STD-01 참고
- Runbook/배포 상태: inventory-api의 파드를 재시작하여 워커 풀 포화 상태를 완화
- 의존 대상 상태: Postgres의 HEALTHY 상태를 확인하였으나 과거 DEGRADED 이슈와 Slow Query 기록 존재
- 증거 도구: grafana Loki 로그, Prometheus 지표, 변경 이력, 의존 대상 헬스 체크

## 원인 후보
1) RB-STD-02 — worker_pool_saturated — signal_count=346, first_seen=2026-10-26T01:10:30+00:00, evidence=true
2) RB-STD-01 — connection_pool_exhausted — signal_count=0, evidence=false
3) RB-STD-03 — request_queue_backlog — signal_count=0, evidence=false
4) RB-STD-04 — external_api_timeout — signal_count=0, evidence=false
5) RB-STD-05 — slow_query — signal_count=0, dependency=postgres, dependency_status=HEALTHY, evidence=false

처리할 원인으로 RB-STD-02를 선택했습니다.

## 조치와 결과
- 조치: restart_pods(inventory-api) 실행, Kubernetes에서 롤링 재시작
- 승인/실행: 실행 승인 받음(CHANGE MODE: GITOPS) 후 실행
- 실행 결과: 재시작된 파드 inventory-api-645dbd655-ghdmc, inventory-api-645dbd655-qxk7가 롤링 방식으로 재시작
- 회복 확인 수치: 에러율 0.0, p95_seconds 0.1316초
- 회복 여부: recovered = true
- 에스컬레이션: 필요 시 Slack 알림 포함, 호출 기록은 별도 문서에 남김

## SLA 영향
- 남은 시

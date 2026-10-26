# B-hang+latency. 파드 멈춤 + 결제 지연 → 재시작 후 공지 (B사)

- 결과: **실패** (175초)
- 작업: http://localhost:18000/tasks/4f36fbe2-23c3-4fd4-b13f-06855423db67
- 진행 기록: [b-hang-latency.log](b-hang-latency.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-02 → RB-STD-04, 실제 RB-STD-02 → RB-STD-04)
- ✅ 원인 후보에 RB-STD-02, RB-STD-04가 있다 (적힌 후보: RB-STD-01, RB-STD-02, RB-STD-03, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_execute_runbook_action restart_pods checkin-api → business_send_customer_notice)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (restart_pods)
- ❌ 고객 공지가 발송됐다
- ✅ 재시작 뒤 결제 지연이 남아 회복되지 않았다고 판정했다
- ✅ 재시작한 새 파드도 결제 지연을 이어받았다 (ConfigMap ['PAYMENT_LATENCY'])
- ✅ 인시던트가 하나만 열렸다 (#3)

## 참고

- 인시던트 #3에 묶인 알림 2개, 작업에 남은 알림 추가 1건

## Agent 보고서 (실행 당시 생성문, 검증된 사실과 구분)

실행기가 보관한 앞부분(최대 1,500자)이다. 판정은 위 검사 결과와 이벤트·감사 기록을 따른다. 보고서의 수치·단위·서술 정확도 전체를 검증한 결과가 아니다.

## 요약
tenant-b checkin-api에서 에러율이 20% 초과 이슈가 발생했고, 외부 의존(payment-gateway)의 지연이 확인되어 먼저 외부 의존 지연(RB-STD-04)을 치료하는 방향으로 진행했습니다. 현재 정상화 여부는 확인되지 않았으며, 남은 신호는 RB-STD-02의 워커 풀 포화 신호와 외부 의존 지연의 지속으로 보입니다.

## 근거
- 메트릭: 에러율 0.94% → 최근 회복 수치 0.00% (기준 0.20%), p95 지연 1.796초에서 재확인 시 2.451초(조치 직후 15초 창 기준)로 확인
- 로그: ERROR/ WARN 다수. 503 worker pool saturated, oldest worker blocked 등
- 의존 대상 상태: payment-gateway DEGRADED, p95 지연 2.127초, callsPerSecond 0.153
- 최근 변경 이력: RB-STD-02 조치 이후 남은 신호 49건, RB-STD-04 외부 의존 지연 신호 11건 새로 포착
- 문서: RB-STD-02, RB-STD-04 런북 참조 및 SLA 문구

## 원인 후보
1) RB-STD-02 - worker_pool_saturated — signal_count=49, first_seen=2026-10-26T00:24:30+00:00, 증상: RB-STD-02 (근거: 여전히 신호 존재, 증상 지속) (evidence: true)
2) RB-STD-04 - external_api_timeout — signal_count=11, first_seen=2026-10-26T00:24:40+00:00, 의존: payment-gateway, 상태 DEGRADED, 의존 지연 신호 지속 (evidence: true)
3) RB-STD-01 - connection_pool_exhausted — signal_count=0 (근거 없음)
4) RB-STD-03 - request_queue_backlog — signal_count=0 (근거 없음)
5) RB-STD-05 - slow_query — signal_count=0 (근거 없음)

- 최우선 원인: RB-STD-04 외부 의존 지연
- 보완 후보: RB-STD-02 워커 풀 포화 (내부 처리 대기)

## 조치와 결과
- 승인 및 조치 이력
  - RB-STD-02 조치 수행: restart_pods(checkin-api) — 실행 EXECUTED, 대상 파드 여러 대를 한 번에 재시작(ALL_AT_ONCE)
  - 외부 의존(DEGRADED) 이슈는 해결되지 않음으로 간주되며, RB-STD-04 개선을 병행 제안
- 실행 결과
  - 조치 상태: RB-STD-02 관련 신호 감소 확인(신호 개수 404→ 49로 감소)되었으나 여전히 남아있음
  - 회복 여부: NOT_RECOVERED (에러율 0.00, p95 지연 2.45초)로 남아 있음
- 고객 공지 발송 시도
  - 메시지 전송 시 검증 오류로 실패(Sent 자체 실패). 에스컬레이션 기록 QUEUED로

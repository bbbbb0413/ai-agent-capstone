# B-hang+latency. 파드 멈춤 + 결제 지연 → 재시작 후 공지 (B사)

- 결과: **통과** (165초)
- 작업: http://127.0.0.1:18000/tasks/2a0f64ac-3124-4bce-aa9e-cd5d265d6843
- 진행 기록: [b-hang-latency.log](b-hang-latency.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-02 → RB-STD-04, 실제 RB-STD-02 → RB-STD-04)
- ✅ 원인 후보에 RB-STD-02, RB-STD-04가 있다 (적힌 후보: RB-STD-01, RB-STD-02, RB-STD-03, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_execute_runbook_action restart_pods checkin-api → business_send_customer_notice)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (restart_pods)
- ✅ 고객 공지가 발송됐다
- ✅ 재시작 뒤 결제 지연이 남아 회복되지 않았다고 판정했다
- ✅ 재시작한 새 파드도 결제 지연을 이어받았다 (ConfigMap ['PAYMENT_LATENCY'])
- ✅ 인시던트가 하나만 열렸다 (#11)

## 참고

- 인시던트 #11에 묶인 알림 2개, 작업에 남은 알림 추가 1건

## Agent 보고서

## 요약
tenant-b checkin-api에서 에러율이 20%를 초과하는 장애가 발생했고, 외부 결제 대행사 연동 지연(RB-STD-04)을 우선 치료하는 방향으로 조치가 진행되었습니다. 현재 에러율은 0.00으로 표기되지만, 회복 여부는 여전히 확정되지 않았습니다.

## 근거
- 메트릭: 에러율 0.941, p95 1.676초 (사건 직전 시점 2026-10-26T01:12:25+00:00 ~ 01:13:10+00:00 구간), 에러율/응답시간 추세가 보고됨
- 로그: 503 워커 풀 포화(활성 200/200, 대기 큐 1000/1000) 및 external_api_timeout 경향(결제 게이트웨이 지연, DEGRADED 상태) 확인
- 변경 이력: 2026-10-26T01:13:19+00:00에 RB-STD-02(워커 풀 재시작) 실행 후 신호 감소 → 재발 여부 확인 중
- 문서: MSP 표준 장애 대응 런북 RB-STD-02, RB-STD-04 참조, 외부 의존 지연이 우선 원인 후보로 제시됨

## 원인 후보
1) RB-STD-04 - external_api_timeout — 신호_count=11, first_seen=2026-10-26T01:13:20+00:00, 의존: payment-gateway, 의존 상태: DEGRADED (의존 지연 지속)  
2) RB-STD-02 - worker_pool_saturated — signal_count=356(처리 대기 증가), first_seen=2026-10-26T01:12:25+00:00, 증상: 워커 풀 포화, 처리 대기 증가  
3) RB-STD-01 - connection_pool_exhausted — 신호 없음  
4) RB-STD-03 - request_queue_backlog — 신호 없음  
5) RB-STD-05 - slow_query — 신호 없음

처리할 원인: RB-STD-04

참고: RB-STD-02도 신호가 확인되나 현재 주 원인은 외부 의존 지연(RB-STD-04)으로 판단됨.

## 조치와 결과
- 조치: RB-STD-02에 따른 워커 풀 재시작 수행(파트너 런북에 따라, 다수 파드 동시 재시작). 파드 재시작으로 신호 감소 확인
- 실행 결과: 조치 직후 확인된 메트릭에서 에러율이 0.0으로 표시되며(p95는 여전히 2.47초대), 회복 여부는 아직 확정되지 않음
- 상태: action_result
  - 상태: SENT(고객 공지 발송)
  - 회복 여부: recovered=false
- 에스컬레이션: P1으로 등록되어 Slack/대응 채널로의 후속 알림 대기
- 참고: 이전에 수행된 조치로 RB-STD-02에 의한 워커 풀 재시작이 신호를 감소시켰으나, 외부 의존 지연(RB-STD-04) 지속 가능성 때문에 근본 해결은 외부 의존 관리가 필요로 판단됨

## SLA 영향
- 도구가 반환한 응답 기한 및 남은 시간 값을 그대로 기재: 남은 시간은 SLA 문서에 따른 remainingMinutes 기준으로 표기

실제 남은 시간 및 기한은 도

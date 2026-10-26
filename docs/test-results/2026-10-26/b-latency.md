# B-latency. 외부 결제 API 지연 → 고객 공지 (B사)

- 결과: **통과** (84초)
- 작업: http://127.0.0.1:18000/tasks/a0d788b8-5b98-463c-af5f-408041611b02
- 진행 기록: [b-latency.log](b-latency.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_send_customer_notice)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 고객 공지가 발송됐다
- ✅ 외부 원인이라 앱 조치를 실행하지 않았다 (0건)

## Agent 보고서

## 요약
 tenant-b의 checkin-api p95 응답시간이 1초를 초과하는 장애가 발생했고 현재 조치는 진행 중이며 외부 의존 지연이 원인으로 판단됩니다. 외부 결제 대행사(payment-gateway) 이슈로 p95가 2.024s 수준까지 상승했고, 외부 의존 신호는 19건이 확인되었습니다.

## 근거
- 메트릭: p95_seconds=2.018s, requests_per_second=2.27, error_rate=0.0, external_api_timeout 신호 19건(first_seen=2026-10-26T01:10:30+00:00), external 의존: payment-gateway (DEGRADED, p95Seconds=2.024, shares=0.98)
- 로그: WARN 다수 및 외부 API 타임아웃 관련 로그 예시 다수(예: payment API timeout, retrying)
  - 예시 로그: WARN [scheduling-1] PaymentGatewayClient - payment API timeout after 1743ms, retrying
- 변경 이력: kubernetes 배포 이력 (opslens/demo-app:0.1.0) 2026-10-23T07:34:28Z
- 문서: MSP 표준 장애 대응 런북 RB-STD-04 외부 의존 지연, RB-STD-02 워커 풀 포화
- 외부 의존 상태 확인: payment-gateway 상태 DEGRADED, p95Seconds 2.024, callsPerSecond 1.855

## 원인 후보
1) RB-STD-04 외부 의존 지연 — external_api_timeout 신호 19건, 의존: payment-gateway, 상태: DEGRADED, first_seen=2026-10-26T01:10:30+00:00, dependency_share=0.98, 근거: true
2) RB-STD-02 워커 풀 포화 — signal_count=0, first_seen=null, 근거: false

- 최종 선정 원인: RB-STD-04
- 증상으로 본 영향: 외부 의존 지연으로 p95 응답시간 상승이 관찰됨

## 조치와 결과
- 승인 및 조치: RB-STD-04에 따른 분석 및 외부 의존지연 문제를 우선으로 다룸
- 실행 여부: 현재 실행 중으로 기록
- 회복 여부: 미확정(현재 p95 약 2.0초대 유지, 에러율 0.0)
- 조치 방향: 외부 의존의 문제이므로 MSP 내부 조치 한계가 있으며, 외부 의존 문제 해결을 우선 대외 협의 및 고객 공지 유지
- 고객 공지 발송: 이미 발송 완료(SENT)

참고: 과거 장애 기록과의 비교에서 외부 의존 지연이 지속적으로 확인되며, 이번 장애에서도 외부 결제 연동 지연과의 관련성이 높음

## SLA 영향
- 도구가 제공한 SLA 관련 수치 그대로 반영
- 남은 시간 및 기한은 SLA 문서의 값에 따라 계산되며, 이번 장애로 인한 영향은 외부 의존 지연에 집중되어 있음

## 권장 다음 행동
- 사람 확인 필요:
  -

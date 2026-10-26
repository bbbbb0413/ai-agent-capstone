# A-latency. 외부 결제 API 지연 → 고객 공지 (A사)

- 결과: **통과** (76초)
- 작업: http://127.0.0.1:18000/tasks/6e8d7aae-3651-492f-97f5-4c5a426e29ba
- 진행 기록: [a-latency.log](a-latency.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_send_customer_notice)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 고객 공지가 발송됐다
- ✅ 외부 원인이라 앱 조치를 실행하지 않았다 (0건)

## Agent 보고서

## 요약
inventory-api p95 응답시간 증가 이슈가 발생했고 외부 의존(API) 지연이 관찰되었습니다. 현재 상태는 조치가 진행 중이며, 고객 공지는 이미 발송되었습니다.

## 근거
- 메트릭: p95_seconds=2.0165s, requests_per_second=3.98, error_rate=0.0 (1분 구간)
- 로그: 외부 API timeout 경고 39건; WARN 레벨 로그에서 외부 결함 지표 확인
- 변경 이력: 2026-10-23T05:47:44Z inventory-api.yaml 배포 커밋 및 장애용 데모 서비스 구성 추가 및 injection 스크립트 언급
- 문서: MSP 표준 장애 대응 런북 RB-STD-04 외부 의존 서비스 지연
- 과거_incident: 장애 #7에서 RB-STD-04 관련 외부 의존 지연 사례 확인

## 원인 후보
1) RB-STD-04 외부 의존 지연 — external_api_timeout, signal_count=40, first_seen=2026-10-26T01:13:15+00:00, evidence=true (처리: RB-STD-04)

## 조치와 결과
- 승인자: admin
- 실행 내용: 외부 의존 지연 원인으로 RB-STD-04를 확인하고 조치 제안, 공지 발송
- 실행 방식: 고객 공지 발송 포함, PR 없이 즉시 조치
- 회복 확인 수치: evidence에 따른 외부 API timeout 39건, p95 2.016s, 0.0의 에러율
- 현재 상태: 조치 진행 중(SENT 공지 발송)이며 내부 모니터링으로 추적 중

## SLA 영향
- 도구가 준 응답 기한 및 남은 시간 값을 그대로 사용한다. 남은 시간 약 59분 내에 재평가 예정.

## 권장 다음 행동
- 외부 의존 서비스 소유자와 의사소통 및 영향 범위 확인
- 외부 API timeout의 재현 여부 확인 및 재발 방지 대책 수립
- 필요 시 HPA/리소스 조정은 RB-STD-03/ RB-STD-04 가이드에 따라 진행

## 조사 과정
- grafana_query_loki_logs: inventory-api 관련 WARN 로그 및 외부 API timeout 확인 (다양한 경고 메시지 포함)
- grafana_query_prometheus: p95_seconds, requests_per_second, error_rate 수치 확인
- business_list_recent_changes: 변경 이력 확인(설정 저장소 gitops/tenants/tenant-a/inventory-api.yaml)
- cause_facts: 각 RB-STD 후보별 신호 수치 및 근거 확인
- business_get_service_inventory: 서비스 의존성 및 관측 지표 확인
- business_send_customer_notice: 고객 공지 발송 확인

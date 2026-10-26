# A-dbslow. DB 자체 지연 → DBA 에스컬레이션 (A사)

- 결과: **통과** (115초)
- 작업: http://127.0.0.1:18000/tasks/ed3ac873-aded-49fe-8f66-888e9ba7fa59
- 진행 기록: [a-dbslow.log](a-dbslow.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_escalate_incident inventory-api DEPENDENCY postgres)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 에스컬레이션 DEPENDENCY postgres 대기열 등록 (있음: DEPENDENCY postgres)
- ✅ 받는 사람에 A사 DBA팀이 있다 (engineer, admin, A사 DBA팀)
- ✅ 고객 공지가 발송됐다
- ✅ DB 자체 지연이라 앱 조치를 실행하지 않았다 (0건)

## Agent 보고서

## 요약
tenant-a의 inventory-api에서 p95 응답시간이 1초를 초과하는 장애가 발생했고, 현재 DB 의존 대상의 장애로 추정되어 에스컬레이션이 QUEUED 상태입니다. 에스컬레이션 대상은 postgres이며, 고객 영향 여부에 따라 공지 여부를 판단합니다.

## 근거
- 메트릭: p95 1.6596초, 요청 수 3.036 초당, 오류율 0% (Prometheus 합계 및 Grafana 요약)
- 로그: slow_query 83건, 예시 메시지 "WARN ... slow query: SELECT * FROM orders WHERE customer_id = ? FOR UPDATE took 1158ms (db=postgres)" (Grafana Loki 로그)
- 변경 이력: 2026-10-23T05:47:44Z — inventory-api.yaml 배포 및 장애용 데모 서비스 추가로 인한 변경
- 문서: MSP 표준 장애 대응 런북 RB-STD-05 의존 대상(DB) 자체 장애, 에스컬레이션 대상은 DB 소유자에게 전달
- 과거 사건: 장애 #7 관련 포스트모터움에서 RB-STD-05를 참조한 이슈 기록 존재
- 승인이력: 에스컬레이션 승인 완료(Approval ID: 66d98e5c-ef64-5a17-a9fe-a986ff30e277)

## 원인 후보
1. RB-STD-05 — 의존 대상(DB) 자체 장애; slow_query 및 DEGRADED 상태의 postgres 의존으로 인한 지연
2. RB-STD-01 — connection_pool_exhausted
3. RB-STD-02 — worker_pool_saturated
4. RB-STD-03 — request_queue_backlog
5. RB-STD-04 — external_api_timeout

처리할 원인: RB-STD-05
병목: DB

## 조치와 결과
- 승인이 필요한 조치: 에스컬레이션 진행(DB 소유자에게 담당 전달, 고객 영향 여부 판단 후 공지 여부 결정)
- 실행 여부: 에스컬레이션이 QUEUED 상태로 기록됨
- 반영 방식: 에스컬레이션 기록은 OpsLens에 저장되었으며, Slack 발송은 별도 검토 후 승인 필요
- 회복 확인 수치: 아직 정상화되지 않음(Recovered: false)

참고: 현재 시스템은 DB 의존 장애로 판단되며, 런북 RB-STD-05에 따라 의존 대상의 소유자에게 에스컬레이션이 진행 중임

## SLA 영향
- SLA에 따른 남은 시간: remainingMinutes 59 (조치가 진행 중인 상태에서의 남은 시간값을 도구가 제공)

## 권장 다음 행동
- A사 DBA팀과의 커뮤니케이션 이후 고객 영향 여부를 판단하고, 영향이 있으면 공지 여부를 결정한다.
- 필요 시 MSSP 운영 정책에 따라 DB 연결 한도 및 모니터링 임계치를 재확인하고, 의존 대상 상태가 정상화될 때까지 모니터링 강화
- 에스컬레이션 진행 상황을 지속적으로 업데이트하고, 고객 공지 여부를 최종 결정한다

## 조사 과정
- grafana

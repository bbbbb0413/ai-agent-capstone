# B-pool+dbslow. 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (B사)

- 결과: **통과** (103초)
- 작업: http://localhost:18000/tasks/171100b0-13dd-46ad-a0b9-de5d234aad58
- 진행 기록: [b-pool-dbslow.log](b-pool-dbslow.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 진단마다 처리할 원인이 기대와 같다 (기대 RB-STD-01, 실제 RB-STD-01)
- ✅ 원인 후보에 RB-STD-01, RB-STD-05가 있다 (적힌 후보: RB-STD-01, RB-STD-02, RB-STD-04, RB-STD-05)
- ✅ 조치를 기대한 순서로 제안했다 (business_escalate_incident reservation-api ACTION_BLOCKED rollback_deployment CONTRACT)
- ✅ 같은 앱 조치를 두 번 하지 않았다 (앱 조치 없음)
- ✅ 에스컬레이션 ACTION_BLOCKED rollback_deployment CONTRACT 대기열 등록 (있음: ACTION_BLOCKED CONTRACT rollback_deployment)
- ✅ 받는 사람에 B사 운영 승인 담당이 있다 (engineer, admin, B사 운영 승인 담당)
- ✅ 롤백이 계약상 불가라 앱 조치를 실행하지 않았다 (0건)
- ✅ 인시던트가 하나만 열렸다 (#4)

## 참고

- 인시던트 #4에 묶인 알림 2개, 작업에 남은 알림 추가 1건

## Agent 보고서 (실행 당시 생성문, 검증된 사실과 구분)

실행기가 보관한 앞부분(최대 1,500자)이다. 판정은 위 검사 결과와 이벤트·감사 기록을 따른다. 보고서의 수치·단위·서술 정확도 전체를 검증한 결과가 아니다.

## 요약
tenant-b reservation-api의 p95 응답시간이 1초를 초과하는 장애가 발생했고, 현재는 에스컬레이션 상태로 롤백 배포가 필요하나 계약상 승인 절차가 남아 있어 실행되지 않은 상태입니다. 배포 직전 이력과 DB 커넥션 풀 고갈이 원인 후보로 제시되었으며, 현재 조치로 에스컬레이션이 QUEUED 상태입니다.

## 근거
- 메트릭: p95=1.342s, 에러율=0.1597, 32건의 connection_pool_exhausted, 30건의 slow_query
  - p95 및 에러율은 Prometheus/Grafana 수치에서 확인
  - 연결 풀 고갈(32건), 느린 쿼리(30건)로 지연 발생 신호 존재
- 로그: reservation-api에서 503/연결 풀 고갈 관련 ERROR/WARN 다수
  - 예시 로그: "HikariPool-1 - Connection is not available, request timed out" 및 503 에러
- 변경 이력: 직전 배포 시점 00:23:04에 opSlens/demo-app:0.1.0 배포
- 문서: MSP 표준 런북 RB-STD-01(커넥션 풀 고갈 원인), RB-STD-03(트래픽 급증 시 처리 용량 확인), RB-B-02 재시작 시 주의, RB-B-03 외부 결제 연동 공지
- 조치 권고 근거: RB-STD-01에 따른 DB 커넥션 풀 고갈 이슈이므로 롤백이 적합하나 계약상 롤백은 고객 승인 필요로 에스컬레이션 필요
- 과거 incident 참조: 장애 #1, #2의 원인 후보/수행 방식과 비슷한 맥락으로 RB-STD-02/ RB-STD-03의 조치 논리 확인

## 원인 후보
1) RB-STD-01 — 연결 풀 고갈(connection_pool_exhausted) — 32건, first_seen 2026-10-26T00:23:00Z
   - 처리 상태: RB-STD-01(원인)로 판단, 병목: 앱
2) RB-STD-05 — 느린 쿼리(slow_query) — 30건, first_seen 2026-10-26T00:22:55Z
   - 처리 상태: 증상으로 포함
3) RB-STD-02 — 작업 스레드 고갈(worker_pool_saturated) — 신호 0
   - 처리 상태: 증상 후보이나 증거 불충분
4) RB-STD-04 — 외부 의존 지연(external_api_timeout) — 의존: payment-gateway, 상태 HEALTHY
   - 처리 상태: 증거 불충분

- 최종 판단: RB-STD-01 우선 처리 예정
- 병목: 앱

## 조치와 결과
- 승인 여부: 승인자(APPROVE) 및 에스컬레이션 QUEUED 상태로 남아 실행 미확정
- 실행 예정 조치: rollback_deployment(직전 배포로 롤백)
- 실행 방식: 변경/실행은 계약상 허용 여부로 아직 확정되지 않아 직접 실행 보류 중
- 회복 확인 여부: 미확인
- 관련 기록: 에스컬레이션 ID 5, 대상 수신자 MSP On-Call 엔지니어/관리자 및 고객 운영 승인 담당

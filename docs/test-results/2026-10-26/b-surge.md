# B-surge. 트래픽 급증 → 확장 불가 에스컬레이션 (B사)

- 결과: **통과** (167초)
- 작업: http://127.0.0.1:18000/tasks/ed116936-a717-4ec9-bf21-308587e6460e
- 진행 기록: [b-surge.log](b-surge.log)

## 판정

- ✅ 작업 완료 (상태 DONE)
- ✅ 기대한 조치를 제안했다 (business_escalate_incident reservation-api ACTION_BLOCKED scale_deployment CONTRACT)
- ✅ 조치 제안이 한 번이다 (1번. 두 번 이상이면 회복 판단을 의심한다)
- ✅ 기대하지 않은 승인 요청이 없다
- ✅ 에스컬레이션 ACTION_BLOCKED scale_deployment CONTRACT 대기열 등록 (있음: ACTION_BLOCKED CONTRACT scale_deployment)
- ✅ 받는 사람에 B사 운영 승인 담당이 있다 (engineer, admin, B사 운영 승인 담당)
- ✅ 고객 공지가 발송됐다
- ✅ 확장이 계약상 불가라 앱 조치를 실행하지 않았다 (0건)

## Agent 보고서

## 요약
tenant-b의 reservation-api p95 응답시간이 1초를 초과하는 장애가 발생했고, 현재는 조치 대기 상태입니다. 앱 대기열(backlog)이 주된 병목으로 판단되며, 계약상 승인이 필요한 레플리카 확장이 필요하나 현재 제한으로 에스컬레이션이 진행 중입니다.

## 근거
- 메트릭: p95 3.914s, 에러율 0.0, 요청당담 45.87 rps
- 로그: 여러 WARN/ERROR 중 “request queue backlog: 291 requests waiting for a processing slot (4/4 slots busy), queue wait 3627ms” 예시 포함
- 변경 이력: 직전 배포 시점 2026-10-26T01:15:12Z, 이미지 opslens/demo-app:0.1.0
- 문서: MSP 표준 런북 RB-STD-03, RB-STD-01, RB-STD-02, RB-STD-04, RB-STD-05; tenant-b 런북 RB-B-02RB-B-01
- 조치 권고 근거: RB-STD-03에 따른 앱 대기열 문제 해결을 위한 레플리카 확장 필요(현재 HPA 미적용, safeMaxReplicas=1로 확장 불가), 또한 계약상 승인이 필요하여 에스컬레이션 진행 중
- 과거 참조: 장애 #4 포멀 포스트모템에서 커넥션 풀 고갈 이슈의 유사 맥락 존재

## 원인 후보
1) RB-STD-03 — 앱 대기열(backlog) 누적으로 인한 처리 지연 (signal_count 37, first_seen 2026-10-26T01:15:50+00:00, evidence: true)  
   - 현재 우선 해결 대상으로 선정

기타 후보들(참고 수준): RB-STD-01, RB-STD-02, RB-STD-04, RB-STD-05

## 조치와 결과
- 승인 상태: APPROVE, 엔지니어가 승인
- 실행 여부: 에스컬레이션 QUEUED 상태, 실행 대기
- 수행 예정 조치(승인 시): RB-STD-03 우선 순위로 앱 대기열 관리 조치
  - 필요 시 파드 스케일 아웃 / 풀 사이즈 조정 (현재 poolSize 20)
  - 필요 시 restart_pods 가능 여부 확인
  - 1–2분 간격으로 p95 및 backlog 변화 모니터링
- 현재 상황: HPA가 설정되지 않아 즉시 확장이 어려움. 계약상 레플리카 확장은 고객 승인 필요로 에스컬레이션 진행 중
- 관련 런북/ 변경 이력:
  - RB-STD-03 런북 내용 반영 예정
  - RB-B-02 재시작 주의: Reservation-api 레플리카 1개일 경우 재시작 시 서비스 중단 가능성 고지
  - SLA 조항에 따른 조치 절차 준수 중

action_result: 에스컬레이션이 QUEUED 상태이며, 시나리오에 따른 조치가 승인 대기 중
change: 없음

## SLA 영향
- 도구가 제공한 남은 시간 값과 기한은 그대로 반영
- 남은 시간: 239분 (tenant의 SLA 잔여 시간 기준)

## 권장 다음 행동
- 고객의 승인을 받아 RB-STD-

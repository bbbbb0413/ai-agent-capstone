# backend 방어선 (계약·구조 제약)

- 결과: **통과** (134초)
- 작업: 없음
- 진행 기록: [guard.log](guard.log)

## 판정

- ✅ A사 HPA 서비스 직접 확장 → HPA 충돌 방지를 위해 거부 (422: order-api의 레플리카는 HPA(order-api, 2~3)가 관리해 직접 바꾸지 않는다. HPA 최대치가 모자라면 ad)
- ✅ A사 HPA 최대치 5 → 안전 상한 4로 거부 (422: adjust_autoscaler의 최대치는 3보다 크고 안전 상한 4 이하여야 한다 (요청 5))
- ✅ B사 Docker 서비스 재시작 → 지원하지 않는 실행 환경으로 거부 (422: settlement-batch의 실행 환경(docker)은 OpsLens가 지원하지 않아 조치할 수 없다. 지원: [kuber)
- ✅ B사 롤백 → 계약상 불가로 거부 (422: 'rollback_deployment'는 B사 예약 플랫폼 계약상 허용되지 않는다. 허용 조치: [restart_pods, n)
- ✅ 거부된 요청은 HPA가 관리하는 레플리카를 바꾸지 않았다 (2 → 2)

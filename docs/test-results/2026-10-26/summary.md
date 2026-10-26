# OpsLens E2E (2026-10-26 10:10)

갱신: 10:20:17

| 묶음 | 시나리오 | 상태 | 시간 | 처음 실패한 항목 | 상세 |
|---|---|---|---|---|---|
| - | 사전 점검 | 통과 | 0초 |  | |
| 공통 | D. 고객사 격리 (권한·감사 로그) | 통과 | 3초 |  | [isolation.md](isolation.md) · [log](isolation.log) |
| A1 | backend 방어선 (안전 상한·실행 환경·계약) | 통과 | 134초 |  | [guard.md](guard.md) · [log](guard.log) |
| 공통 | 문서 질문 → RAG 답변 | 통과 | 4초 |  | [question.md](question.md) · [log](question.log) |
| 공통 | 열린 인시던트가 없는 질문 → 이상 없음 | 통과 | 4초 |  | [no-issue.md](no-issue.md) · [log](no-issue.log) |
| 공통 | 근거 문서 업로드 → 색인 → 검색 반영 → 되돌리기, 권한, 표준 런북 검사 | 통과 | 11초 |  | [documents.md](documents.md) · [log](documents.log) |
| A2 | A사 잘못된 설정 배포 → 롤백 PR → 병합 → 동기화 (GITOPS) | 통과 | 231초 |  | [a-pool.md](a-pool.md) · [log](a-pool.log) |
| A3 | A사 파드 멈춤 → 순차 재시작 | 통과 | 162초 |  | [a-hang.md](a-hang.md) · [log](a-hang.log) |
| A1 | A사 트래픽 급증 → HPA 최대치 4 PR → 병합 → 확장 | 통과 | 164초 |  | [a-surge.md](a-surge.md) · [log](a-surge.log) |
| A1 | A사 트래픽 급증(안전 상한 초과) → 최대치 4 후 미회복 → 에스컬레이션 | 통과 | 190초 |  | [a-surge-max.md](a-surge-max.md) · [log](a-surge-max.log) |
| A3 | A사 외부 결제 지연 → 고객 공지 | 통과 | 76초 |  | [a-latency.md](a-latency.md) · [log](a-latency.log) |
| A3 | A사 DB 자체 지연 → DBA 에스컬레이션 | 통과 | 115초 |  | [a-dbslow.md](a-dbslow.md) · [log](a-dbslow.log) |
| B1 | B사 파드 멈춤 → 재시작 (전체 중단 영향 표시) | 통과 | 173초 |  | [b-hang.md](b-hang.md) · [log](b-hang.log) |
| B2 | B사 외부 결제 지연 → 고객 공지 | 통과 | 84초 |  | [b-latency.md](b-latency.md) · [log](b-latency.log) |
| B1 | B사 잘못된 배포 → 롤백 계약상 불가 → 에스컬레이션 | 통과 | 102초 |  | [b-pool.md](b-pool.md) · [log](b-pool.log) |
| B1 | B사 트래픽 급증 → 확장 계약상 불가 → 에스컬레이션 | 통과 | 167초 |  | [b-surge.md](b-surge.md) · [log](b-surge.log) |
| B2 | B사 파드 멈춤 + 결제 지연 → 재시작 후 고객 공지 (겹친 장애) | 통과 | 165초 |  | [b-hang-latency.md](b-hang-latency.md) · [log](b-hang-latency.log) |
| A2 | A사 DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (겹친 장애) | 통과 | 84초 |  | [a-dbslow-backlog.md](a-dbslow-backlog.md) · [log](a-dbslow-backlog.log) |
| B1 | B사 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (겹친 장애) | 통과 | 142초 |  | [b-pool-dbslow.md](b-pool-dbslow.md) · [log](b-pool-dbslow.log) |

# OpsLens E2E (2026-10-26 09:22)

갱신: 09:25:54

| 묶음 | 시나리오 | 상태 | 시간 | 처음 실패한 항목 | 상세 |
|---|---|---|---|---|---|
| - | 사전 점검 | 통과 | 0초 |  | |
| B2 | B사 파드 멈춤 + 결제 지연 → 재시작 후 고객 공지 (겹친 장애) | 실패 | 175초 | 고객 공지가 발송됐다 | [b-hang-latency.md](b-hang-latency.md) · [log](b-hang-latency.log) |
| A2 | A사 DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (겹친 장애) | 통과 | 95초 |  | [a-dbslow-backlog.md](a-dbslow-backlog.md) · [log](a-dbslow-backlog.log) |
| B1 | B사 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (겹친 장애) | 통과 | 103초 |  | [b-pool-dbslow.md](b-pool-dbslow.md) · [log](b-pool-dbslow.log) |

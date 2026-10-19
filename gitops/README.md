# gitops: 고객사 환경 (Kubernetes kind)

OpsLens가 조치하는 **고객사 쪽** 환경이다. OpsLens 플랫폼(docker compose)과는 별개이며, 결정 근거는 [ADR 0001](../docs/adr/0001-tenant-infra-actions-hybrid-environment.md)에 있다.

| 경로 | 무엇 | 누가 바꾸나 |
|---|---|---|
| `cluster/` | kind 클러스터 설정, 고객사 네임스페이스, 고객사별 OpsLens 권한(ServiceAccount·Role), 관측 수집기, ArgoCD Application | 사람 (클러스터 구성 변경) |
| `tenants/tenant-a/` | **A사(GitOps)의 원하는 상태.** ArgoCD가 `main` 브랜치의 이 경로를 클러스터와 동기화한다 | **PR로만.** OpsLens backend는 이 경로만 고치는 PR을 만든다 |
| `tenants/tenant-b/` | B사(직접 운영)의 초기 배포 매니페스트. ArgoCD는 보지 않는다 | 처음 한 번 `scripts/cluster-up.sh`가 적용. 이후 조치는 OpsLens가 API로 직접 |

## 실행

```
scripts/cluster-up.sh     # kind 클러스터 생성, compose 네트워크 연결, ArgoCD 설치, 고객사 배포
scripts/cluster-down.sh   # 클러스터 삭제
```

compose 스택(`docker compose up -d`)이 먼저 떠 있어야 한다. 고객사 서비스의 메트릭과 로그를 compose의 Prometheus·Loki로 보내기 때문이다.

## 주의

- ArgoCD는 GitHub의 `main`을 본다. `tenants/tenant-a/`의 변경은 **푸시된 뒤에야** 클러스터에 반영된다.
- 시나리오 A(잘못된 설정 배포)와 롤백 PR은 이 저장소의 `main` 기록에 커밋으로 남는다.
- 고객사별 접속 토큰은 `.local/tenants/`에 만들어지고 git에 올라가지 않는다.

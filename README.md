# OpsLens

MSP(Managed Service Provider)가 여러 고객사의 장애를 함께 대응하도록 돕는 AI Agent다. 알림이 오면 Agent가 고객사의 메트릭·로그·변경 이력을 조사해 원인을 찾고, 고객사 계약과 시스템 구조 안에서 할 수 있는 조치를 제안한다. 사람이 승인하면 고객사 운영 방식에 맞게 반영한다(GitOps 고객사는 설정 저장소 PR, 직접 운영 고객사는 Kubernetes API). 할 수 없는 조치는 다른 조치로 대신하지 않고 담당자에게 넘긴다.

## 무엇을 하나

| 기능 | 내용 |
|---|---|
| 장애 진단 | LangGraph Agent가 MCP 도구(메트릭·로그 조회, 변경 이력, 서비스 카탈로그, 의존 대상 상태)를 골라 원인을 찾는다 |
| 조치 판단 | MSP 공통 런북(RB-STD-01~07)으로 장애 형태를 정하고, 서비스 카탈로그의 구조 제약(안전 상한, 무중단 재시작 여부)과 고객사 계약을 backend가 검사한다 |
| 사람 승인 | 조치·에스컬레이션·고객 공지는 모두 승인 카드에서 사람이 승인한다 |
| 고객사별 반영 | A사(GITOPS): 설정 저장소 PR → 병합 → ArgoCD 동기화. B사(DIRECT): Kubernetes API로 바로 |
| 고객사 격리 | 고객사마다 데이터, 문서, 네임스페이스 권한, 관측 데이터가 분리되고 다른 고객사 접근은 거부·감사된다 |
| 작업 전달 | Kafka SLA 등급별 대기열(Gold·Silver), Transactional Outbox, 실행 임대로 유실·중복을 막는다 |

데모 고객사:

- **A사 (온라인 쇼핑몰, Gold, GITOPS):** `order-api`. HPA 최소 2·최대 3, 안전 상한 4. 허용 조치: 재시작, 확장, 롤백, HPA 최대치 조정
- **B사 (예약 플랫폼, Silver, DIRECT):** `reservation-api`. 레플리카 1. 허용 조치: 재시작만(확장·롤백은 B사 승인 필요)

## 구성

```
OpsLens 플랫폼 (docker compose)                       고객사 환경 (Kubernetes kind)
  frontend (Vue 3)  ─▶ backend (Spring Boot) ───────▶  tenant-a: order-api ◀─ ArgoCD ◀─ gitops/tenants/tenant-a (GitHub)
                        │  ▲  업무 규칙·조치 반영         tenant-b: reservation-api
                        ▼  │                            traffic: 부하 발생기
                        Kafka ─▶ agent (FastAPI + LangGraph)
                                  ├─ business-mcp ─▶ backend /internal
                                  ├─ rag-mcp (pgvector 문서 검색)
                                  └─ mcp-grafana ─▶ Prometheus·Loki
  postgres (app·agent·docs 스키마 분리), alertmanager ─▶ backend
```

자세한 설계는 [docs/](docs/README.md)에 있다: [기획안](docs/OpsLens-기획안.md), [ADR](docs/adr/), [Harness 명세](docs/harness-spec.md), [테스트](docs/testing.md).

| 경로 | 내용 |
|---|---|
| `front/` | 운영 화면, 승인 카드, 고객사 포털 |
| `server/spring/backend/` | 인증, 업무 API, 조치 반영 어댑터, 작업 전달 대기열 |
| `server/spring/demo-app/` | 고객사 서비스 역할의 데모 앱(장애 주입 관리 API, 부하 발생 모드) |
| `server/python/agent/` | Agent Graph, Harness, Kafka 소비자, 마이그레이션 |
| `server/python/business-mcp/`, `rag-mcp/`, `indexer/` | MCP 서버와 문서 색인 |
| `gitops/` | 고객사 환경: kind 설정, A사 원하는 상태, B사 초기 배포 |
| `data/documents/` | RAG가 색인하는 공통 런북·고객사 계약서·서비스 런북 |
| `scripts/` | 클러스터 생성·삭제, 장애 주입, E2E |

## 환경 세팅

### 준비물

- Docker Desktop (메모리 8GB 이상 권장)
- [kind](https://kind.sigs.k8s.io/), `kubectl`
- Python 3.12 이상 (E2E·장애 주입 스크립트, 표준 라이브러리만 쓴다)
- OpenAI API 키
- GitHub fine-grained 토큰: 이 저장소에 Contents·Pull requests 쓰기 권한. A사 조치 PR과 E2E 임시 브랜치에 쓴다

> ArgoCD와 backend는 GitHub의 `bbbbb0413/ai-agent-capstone` 저장소를 본다. 포크해서 쓰면 세 곳을 바꾼다: `gitops/cluster/argocd-tenant-a.yaml`의 `repoURL`, backend `application.yml`의 `opslens.gitops.repo`, `scripts/e2e.py`·`scripts/inject.py`의 `REPO`.

### 1. 환경변수

```bash
cp .env.example .env
# .env에 OPENAI_API_KEY, GITHUB_TOKEN을 넣는다. 나머지 값은 로컬 개발용 샘플이다
```

`.env`는 git에 올리지 않는다. 작업 전달 방식 기본값은 `DISPATCH_MODE=kafka`이고, `http`로 바꾸면 Kafka 없이 agent를 바로 호출한다.

### 2. OpsLens 플랫폼 기동

```bash
docker compose up -d --build
```

- `agent-migrate`가 agent 스키마 마이그레이션을 한 번 실행하고 끝난 뒤 agent가 뜬다(`Exited (0)`이 정상).
- backend는 기동할 때 Flyway 마이그레이션(V1~V12)을 실행한다.
- Grafana·PostgreSQL·Prometheus 포트를 열어 보려면 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`를 쓴다.

### 3. 고객사 환경 (kind + ArgoCD)

```bash
scripts/cluster-up.sh
```

kind 클러스터를 만들어 compose 네트워크에 붙이고, metrics-server·ArgoCD를 설치한 뒤 A사·B사 서비스와 부하 발생기를 배포한다. HPA가 첫 CPU 지표를 받고 ArgoCD가 `Synced/Healthy`가 될 때까지 기다린 뒤 끝난다(약 2분). 고객사별 접속 토큰은 `.local/tenants/`에 만들어진다(git에 올리지 않는다).

### 4. 문서 색인

```bash
docker compose --profile tools run --rm indexer
```

`data/documents/`를 Embedding해 RAG DB에 넣는다. 문서가 바뀐 커밋을 받은 뒤에도 다시 실행한다.

### 5. 접속

<http://localhost> 에 로그인한다.

| 계정 | 비밀번호 | 역할 |
|---|---|---|
| `admin` | `admin1234` | MSP 관리자 (고객 공지 승인 포함) |
| `engineer` | `engineer1234` | MSP 엔지니어 (조치 승인) |
| `viewer-a` | `viewer1234` | A사 담당자 (A사 포털만) |
| `viewer-b` | `viewer1234` | B사 담당자 (B사 포털만) |

장애를 손으로 넣어 보려면:

```bash
scripts/inject.sh tenant-b hang     # B사 파드 멈춤 → 알림 → 인시던트·Agent 작업이 생긴다
scripts/inject.sh tenant-b clear    # 원래대로
```

장애 종류: `pool`(잘못된 설정 배포), `hang`(작업 스레드 고갈), `latency`(외부 결제 API 지연), `dbslow`(DB 자체 지연), `surge`(트래픽 급증), `clear`.

### 정리와 전체 초기화

```bash
scripts/cluster-down.sh                                   # 고객사 클러스터 삭제
docker compose --profile tools down -v --remove-orphans   # 컨테이너와 볼륨(DB·로그) 삭제
```

처음부터 다시 구성하려면 위 두 줄 뒤에 2~4단계를 다시 실행한다.

## E2E 테스트

장애 주입부터 알림, Agent 진단, 승인, 조치 반영, 회복 확인, 보고서·공지까지 실제로 돌리고 판정한다. 환경 세팅 2~4단계가 끝난 상태에서 실행한다.

```bash
python3 scripts/e2e.py                    # 전체 14개 (약 30~40분)
python3 scripts/e2e.py --quick            # PR이 없는 빠른 확인: guard a-hang a-dbslow b-surge
python3 scripts/e2e.py b-hang a-dbslow    # 고른 시나리오만
python3 scripts/e2e.py --list             # 시나리오 목록
```

| 묶음 | 시나리오 | 기대 결과 |
|---|---|---|
| 공통 | `isolation` `question` `no-issue` | 고객사 격리, 계약 문서 질문(RAG), 장애가 없을 때 "이상 없음" |
| A사 | `a-hang` | 파드 순차 재시작(무중단) → 회복 |
| A사 | `a-latency` | 외부 결제 지연 → 앱 조치 없이 고객 공지 |
| A사 | `a-dbslow` | DB 자체 지연 → A사 DBA 에스컬레이션 + 공지 |
| A사 | `a-pool` | 잘못된 설정 배포 → 롤백 PR → 병합 → 동기화 → 회복 |
| A사 | `a-surge` | 트래픽 급증 → HPA 최대치 4 PR → 병합 → 확장 → 회복 |
| A사 | `a-surge-max` | 안전 상한으로도 부족 → 구조 제약 에스컬레이션 + 공지 |
| A사 | `guard` | Agent를 거치지 않은 계약·구조 위반 요청이 backend에서 422 |
| B사 | `b-hang` | 재시작(전체 중단 영향 표시) → 회복 |
| B사 | `b-latency` | 외부 결제 지연 → 재시작 없이 고객 공지 |
| B사 | `b-pool` | 잘못된 배포 → 롤백이 계약상 불가 → 에스컬레이션 + 공지 |
| B사 | `b-surge` | 트래픽 급증 → 확장이 계약상 불가 → 에스컬레이션 + 공지 |

- **사전 점검:** 서비스 기동, 고객사 배포, ArgoCD 동기화, 문서 색인, 부하 발생기·HPA 평소 상태, 진행 중인 작업·열린 인시던트 없음을 확인한다. 실패하면 이유를 보여 주고 멈춘다.
- **실행 순서:** 공통 묶음 → A사·B사 묶음을 동시에. 한 고객사 안에서는 순서대로 돌고, 앞 장애의 인시던트가 닫힌 뒤 다음 장애를 넣는다.
- **승인:** 스크립트가 제안을 검사해 기대한 조치만 승인하고 나머지는 거절한다. 실행 동안 backend 자동 승인을 끈다.
- **main을 건드리지 않는다:** 실행 동안 A사 설정 저장소 기준을 임시 브랜치 `e2e/<시각>`로 바꾸고, 끝나면(실패·중단 포함) `main`으로 되돌리고 지운다. 중단 후 남았으면 `python3 scripts/e2e.py --cleanup`.
- **결과:** `.local/e2e/<시각>/summary.md`(실행 중에도 갱신)와 시나리오별 `<키>.log`·`<키>.md`(판정, 작업 링크, Agent 보고서).

옵션: `--serial`(묶음을 하나씩), `--no-merge`(A사 PR을 사람이 GitHub에서 병합), `--auto-approve`(backend 자동 승인 설정을 그대로), `--timeout 초`(시나리오당 최대 시간, 기본 600).

## 단위 테스트

```bash
cd server/spring/backend && ./gradlew test                                   # backend
cd server/python/agent && uv run python -m unittest discover -s tests       # agent
cd front && npm test                                                         # frontend
```

테스트 종류와 DB 통합 테스트 실행 방법은 [docs/testing.md](docs/testing.md)에 있다.

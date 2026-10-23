# OpsLens

MSP(Managed Service Provider)가 여러 고객사의 장애를 함께 대응하도록 돕는 AI Agent다. 알림이 오면 Agent가 고객사의 메트릭·로그·변경 이력을 조사해 원인을 찾고, 고객사 계약과 시스템 구조 안에서 할 수 있는 조치를 제안한다. 사람이 승인하면 고객사 운영 방식에 맞게 반영한다(GitOps 고객사는 설정 저장소 PR, 직접 운영 고객사는 Kubernetes API). 할 수 없는 조치는 다른 조치로 대신하지 않고 담당자에게 넘긴다.

## 무엇을 하나

| 기능 | 내용 |
|---|---|
| 장애 진단 | LangGraph Agent가 MCP 도구(메트릭·로그 조회, 변경 이력, 서비스 카탈로그, 의존 대상 상태)를 골라 원인을 찾는다 |
| 과거 장애 참고 | 종료된 장애의 완료 보고서를 자동 색인하고, 다음 진단에서 같은 고객사의 과거 사례를 검색해 현재 근거와 비교한다 |
| 조치 판단 | MSP 공통 런북(RB-STD-01~07)으로 장애 형태를 정하고, 서비스 카탈로그의 구조 제약(안전 상한, 무중단 재시작 여부)과 고객사 계약을 backend가 검사한다 |
| 사람 승인 | 조치·에스컬레이션·고객 공지는 모두 승인 카드에서 사람이 승인한다 |
| Slack 내부 알림 | 검토 후 발송하고, 확인자·담당자·처리 완료와 이력을 추적한다 |
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
| `data/documents/` | 근거 문서의 처음 데이터(시드). 기동할 때 문서 저장소에 없는 문서만 올라간다 |
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

**내부 호출 토큰.** 서비스끼리 부를 때 호출마다 다른 토큰을 쓴다. 모두 필수이고, 비어 있으면 compose가 기동하지 않으며 두 값이 같으면 backend가 기동하지 않는다.

| 변수 | 호출 |
|---|---|
| `BACKEND_AGENT_TOKEN` | backend → agent (작업 실행·재개) |
| `AGENT_BACKEND_TOKEN` | agent → backend (진행 이벤트, 결과, 작업 토큰 발급) |
| `AGENT_MCP_TOKEN` | agent → MCP Server |
| `MCP_BACKEND_TOKEN` | MCP Server → backend |
| `INDEXER_BACKEND_TOKEN` | postmortem-indexer·document-indexer → backend (회고 읽기, 문서 색인 API만) |
| `GITOPS_WEBHOOK_TOKEN` | ArgoCD 알림 → backend (`cluster-up.sh`가 클러스터에 넣는다) |
| `DEMO_ADMIN_TOKEN` | 장애 주입 → 데모 앱 관리 API (`cluster-up.sh`가 클러스터에 넣는다) |

고객사는 이 토큰이 아니라 **작업 토큰**이 정한다. agent가 작업을 실행할 때마다 backend에서 받는 서명 토큰이고, backend와 rag-mcp는 그 안의 고객사만 믿는다. 서명 키는 backend가 기동할 때 만들므로 환경변수가 없다.

**근거 문서 저장소(S3 호환, SeaweedFS).** `DOCUMENTS_S3_ACCESS_KEY`·`DOCUMENTS_S3_SECRET_KEY`(backend 전용, 문서 버킷만 읽기·쓰기)가 필수다. `.env.example` 맨 아래의 샘플 값을 `.env`에 넣는다. 저장소 계정은 backend만 갖고, indexer와 rag-mcp는 backend 내부 API로 원본을 받는다. MinIO는 2025년 10월 커뮤니티 이미지 배포를 멈춰 쓰지 않는다.

토큰을 바꾼 뒤에는 `docker compose up -d --build`로 다시 띄우고 `scripts/cluster-up.sh`를 다시 실행해 클러스터 시크릿도 새 값으로 바꾼다(이미 떠 있는 클러스터에서 다시 실행해도 된다).

### 2. OpsLens 플랫폼 기동

```bash
docker compose up -d --build
```

- `agent-migrate`가 agent 스키마 마이그레이션을 한 번 실행하고 끝난 뒤 agent가 뜬다(`Exited (0)`이 정상).
- backend는 기동할 때 Flyway 마이그레이션(V1~V18)을 실행하고, `data/documents/`의 문서 중 문서 저장소에 없는 것만 버전 1로 올린다(시드).
- `seaweedfs-init`이 문서 버킷을 만들고 끝난다(`Exited (0)`이 정상). backend 계정은 `seaweedfs`가 기동할 때 환경변수로 만든다.
- Grafana·PostgreSQL·Prometheus 포트를 열어 보려면 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`를 쓴다.

### 3. 고객사 환경 (kind + ArgoCD)

```bash
scripts/cluster-up.sh
```

kind 클러스터를 만들어 compose 네트워크에 붙이고, metrics-server·ArgoCD를 설치한 뒤 A사·B사 서비스와 부하 발생기를 배포한다. HPA가 첫 CPU 지표를 받고 ArgoCD가 `Synced/Healthy`가 될 때까지 기다린 뒤 끝난다(약 2분). 고객사별 접속 토큰은 `.local/tenants/`에 만들어진다(git에 올리지 않는다).

### 4. 근거 문서 (런북·SLA 계약서)

문서는 화면의 **근거 문서** 메뉴에서 올리고 버전을 관리한다(`docs/specs/SPEC-document-store.md`).

- 형식: Markdown, 텍스트, PDF(텍스트 PDF, 스캔 문서 제외), Word(docx). 공통 표준 런북도 같다.
- 권한: 공통 문서는 MSP 관리자만, 고객사 문서는 관리자와 그 고객사를 담당하는 엔지니어만 올리기·새 버전·되돌리기·삭제를 한다.
- 원본은 S3 저장소(SeaweedFS)에 버전별로 남는다. 되돌리기는 고른 버전을 복사한 새 버전을 만든다. 삭제하면 검색에서 빠지고 버전 파일은 남는다.
- 올리면 backend가 원본을 저장하고 Kafka `documents.index`로 색인을 요청한다. `document-indexer`가 형식별로 파싱해(Markdown `#`, Word 제목 스타일, PDF 글자 크기·조항 번호) 제목 경로 단위로 조각을 만들고 임베딩한다. 몇 초 안에 화면의 상태가 "색인 완료"가 된다.
- 색인에 실패하면 단계와 사유가 화면에 남고, 검색은 마지막으로 성공한 버전으로 계속된다. 임베딩 API 오류처럼 다시 시도할 수 있는 실패는 5분 뒤 다시 시도한다.
- 공통 표준 런북은 조항 번호(`RB-STD-01`~`07`)가 지금 검색 버전과 같아야 색인된다. agent의 조치 검사가 조항 번호에 묶여 있기 때문이다.
- Kafka 없이(`DISPATCH_MODE=http`) 띄우면 `document-indexer`가 5초마다 대기 목록을 본다.

`postmortem-indexer`는 기본 compose 기동 시 함께 실행되며, **매 순회 후 60초 간격**으로 종료된 장애의
완료 보고서를 backend에서 읽어 RAG에 반영한다. 처음 실행할 때 기존 보고서도 전체 페이지를 순회한다.
보고서 내용·임베딩 모델이 같으면 다시 임베딩하지 않고, 수정되면 해당 문서의 조각을 교체한다.
회고는 파일을 만들지 않고 `docs.chunks`에 `postmortem-incident-<인시던트 번호>`로 저장한다(자동 회고 전용 ID).

- 대상은 `RESOLVED`이고 종료 시각·보고서가 있으며, 같은 고객사의 `DONE` 작업 답변과 보고서가 일치하는 장애다.
- 고객사는 `data/documents/`의 디렉터리에서 찾는다(`_common` 제외). 별도로 지정하려면
  `.env`에 `POSTMORTEM_TENANTS=tenant-a,tenant-b`를 넣는다.
- 회고는 **자동 수집한 미검토 보고서**다. 과거 추정 원인이나 조치 결과를 현재 장애의 확정 사실로 취급하지 않는다.
- 첫 진단에서 같은 고객사의 회고 조각 최대 2개를 검색한다. 현재 인시던트의 회고는 제외하며,
  회고가 없거나 검색에 실패하면 기존 메트릭·로그 진단을 계속한다. 진행 화면에는 `과거 장애 검색`이 표시된다.

기존 환경에 이 변경을 반영하거나 한 번만 동기화하려면:

```bash
docker compose up -d --build backend agent rag-mcp frontend postmortem-indexer document-indexer
docker compose run --rm --no-deps postmortem-indexer /app/.venv/bin/python -m indexer.postmortems
docker compose logs --tail=30 postmortem-indexer
```

실제 임베딩 API 호출에는 설정된 OpenAI API 키를 사용한다. 문서 색인(`document-indexer`)과 회고 색인(`postmortem-indexer`)의 모델·차원은 같아야 한다.

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

### 서비스 의존성과 장애 영향 분석

MSP 관리자·엔지니어는 **운영 개요 → 장애 영향 분석**에서 고객사를 선택한다.
서비스와 DB·외부 API의 연결도를 보고, 의존 대상을 클릭하면 이를 사용하는 모든 등록 서비스를 확인한다.
`internal_service` 관계는 역방향으로 추적하여 간접 연결도 표시한다. 연결 경로는 가장 짧은 경로 하나를 보여 준다.

- **영향 가능 범위**: 카탈로그에 등록된 직접·간접 연결. 원인을 확정한 결과가 아니다.
- **이상 관측**: 최근 1분 요청이 있고 에러율 5% 이상 또는 p95 1초 이상인 서비스.
- **관측 정상**: 요청·에러율·p95가 모두 확인되고 기준 미만인 서비스.
- **확인 불가**: 데이터 없음, 호출 없음, 지표 조회 실패. 정상으로 대체하지 않는다.
- 의존 대상은 호출 서비스별 p95를 표시한다. 0.3초 이상은 호출 지연, 3초 이상은 심한 호출 지연이다.
  여러 호출자 중 지연이 있으면 대상 노드에도 표시하고, 지연 없이 일부만 데이터가 없으면 확인 불가로 표시한다.
  이 값은 앱에서 측정한 호출 지연이며 대상 자체의 가동 여부나 근본 원인을 확정하지 않는다.

화면은 30초마다 갱신한다. API는 `GET /api/tenants/{tenantId}/impact`이며 고객사 접근 권한을 검사한다.
서비스 노드를 클릭하면 요청량·에러율·응답 시간 상세를 볼 수 있다. Jev·LLM 호출이나 인프라 변경은 하지 않는다.
메트릭은 기존 `demo_orders_seconds_*`, `demo_dependency_seconds_*`를 고객사·서비스별로 집계한다.
다른 서비스는 같은 계측 규약과 `tenant`, `service`, `dependency` 라벨을 제공해야 상태를 표시할 수 있다.
로컬 실행 시 `PROMETHEUS_URL`로 모니터링 서버 주소를 지정할 수 있다(기본 `http://prometheus:9090`).
현재 데모 카탈로그는 A사의 주문 API→DB, B사의 예약 API→결제 서비스와 의존 관계 없는 정산 배치다.
여러 서비스의 공동 장애를 실측하려면 실제 서비스를 계측하고 카탈로그에 관계를 등록해야 한다.

### 로그·에러 화면 첨부 분석

운영 개요의 **Agent에게 묻기**에서 고객사를 고르고 로그·에러 화면을 첨부한 다음 **첨부 자료 분석**을 누른다.
질문을 비워도 첨부 자료 분석을 시작할 수 있다. 파일 선택 후 이미지 미리보기·삭제가 가능하며 고객사를 바꾸면 첨부는 비워진다.
작업 상세에는 업로드한 파일 목록·다운로드와 분석 진행·결과가 남는다.

- 형식: PNG/JPEG 이미지, UTF-8 TXT/LOG/JSON/JSONL/NDJSON/CSV 로그. 최대 3개, 이미지 4MB/개,
  텍스트 512KB/개, 합계 8MB. 이미지는 1600만 픽셀·한 변 8192픽셀 이내다. 서버도 내용을 검증한다.
- 긴 로그는 파일별 앞뒤 총 약 16,000자를 줄 번호와 함께 분석하고 누락 범위를 표시한다.
  텍스트의 일부 키·비밀번호·이메일·토큰 패턴은 모델 입력 전에 가린다. 완전한 비밀 탐지를 보장하지 않으며 이미지 속 민감정보는 첨부 전에 가려야 한다.
- 이미지 내용은 기존 OpenAI 모델에 이미지 입력으로 전달한다. `OPENAI_API_KEY`와 이미지 입력 지원 모델이 필요하며
  기존 `LLM_MODEL`/`FALLBACK_MODEL` 설정을 그대로 사용한다. [공식 이미지 입력 안내](https://developers.openai.com/api/docs/guides/images-vision).
- 결과는 파일에서 확인한 사실·파일명/줄 번호·원인 후보·현재 지표 비교·추가 확인 방법과 검색된 런북 근거를 구분한다.
  연결되지 않은 모니터링·문서는 확인 불가/미대조로 다룬다. 첨부 분석은 장애 등록·복구 조치·공지 발송을 실행하지 않는다.
- 파일은 V15의 작업별 DB 저장소에 보관하며 MSP 권한과 작업 고객사 권한을 모두 검사한다.
  작업·첨부·전달 대기열을 같은 트랜잭션으로 저장한다. Kafka에는 본문을 넣지 않고 Agent가 agent 전용 토큰으로 읽는다.
  이미지 본문은 Graph checkpoint와 해당 이미지 모델 호출의 LangSmith tracing에 복제하지 않는다.
- 보관 기간 자동 삭제는 없으며 작업 삭제 시 DB 외래 키의 cascade로 첨부가 함께 삭제된다.
  원본은 DB에 저장되므로 운영에서는 접근 권한과 DB 백업·보관 정책을 적용한다.

API: `POST /api/ai/tasks/with-attachments` (multipart: tenantId, question, attachments),
`GET /api/ai/tasks/{taskId}/attachments`, `GET /api/ai/tasks/{taskId}/attachments/{id}`.
추가 적용에는 backend·agent·frontend 재빌드가 필요하며 backend 시작 시 V15가 적용된다.

### Slack 연결과 발송

연결 정보는 나중에 관리자 화면에서 입력할 수 있다. 비어 있어도 OpsLens의 장애 대응 기능은 동작한다.

1. Slack 앱의 **Incoming Webhooks**에서 내부 운영 채널을 선택해 Webhook URL을 만든다.
   [Slack 공식 설정 안내](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
2. OpsLens 관리자 계정으로 로그인하고 **운영 개요 → 설정 → Slack 내부 담당자 알림**으로 이동한다.
3. 고객사를 고른 뒤 채널 이름(관리용)과 Webhook URL을 입력하고 **Slack 발송 사용**을 켜서 저장한다.
   실제 수신 채널은 Slack에서 URL을 만들 때 선택한 채널이다. 관리자 입력 채널 이름은 표시용이다.
4. 장애 작업 상세의 **Slack 발송 → Slack 발송 검토**에서 문안을 확인·수정한다.
5. **승인하고 Slack 발송**을 누르면 발송 대기열에 들어가며, 화면에 대기·발송 중·완료·실패 상태가 표시된다.

설정 저장은 외부 메시지를 보내지 않는다. 기존 호출 기록도 자동 발송되지 않으며 사람이 선택해 검토해야 한다.
기존 Agent의 에스컬레이션 승인은 호출 기록을 만드는 단계이고, 외부 Slack 발송 승인은 그 뒤 별도 단계다.
테스트 자동 승인을 켜도 Slack은 사용자 JWT로 인증된 MSP 관리자·엔지니어가 직접 승인해야 한다.
첫 버전의 범위는 **내부 담당자 채널 알림**이며, 고객 공지의 외부 발송·이메일·개인 DM은 포함하지 않는다.

- Webhook URL은 DB에 AES-GCM으로 암호화하고 조회 API·화면·감사 로그에 원문을 돌려주지 않는다.
  암호화 키는 `SLACK_ENCRYPTION_KEY`를 사용할 수 있으며, 생략하면 기존 `JWT_SECRET`에서 용도를 분리해 유도한다.
  이 키를 변경하면 저장한 Webhook URL을 다시 입력해야 한다. URL은 `.env`에 넣을 필요가 없다.
- 설정이 변경되면 대기 중인 발송은 취소하며 새 채널에 맞춰 다시 검토한다. 이미 전송 중인 요청은 취소되지 않을 수 있다.
- 429 응답은 Slack의 `Retry-After`를 따라 최대 5회까지 시도한다. 응답 유실·타임아웃·서버 오류는
  중복 발송을 피하려고 `수신 여부 확인 필요`로 남기고 자동 재전송하지 않는다.
- 장애 조치 권한·승인과 Slack 발송 권한은 별도로 유지한다. 발송 기록에는 승인자·승인 문안·채널·결과를 보관한다.

기존 환경에서는 `docker compose up -d --build backend frontend`로 적용한다. 추가 의존성은 없고
backend가 시작하면서 V13·V14 마이그레이션을 적용한다.

### Slack 담당자 확인과 처리 추적

발송 완료한 알림에는 `미확인 → 확인됨 → 담당 처리 중 → 처리 완료` 상태가 표시된다.
**확인했습니다**는 확인자와 시각을 기록하고, **담당 시작**은 누른 사람을 담당자로 배정한다.
확인을 생략하고 바로 담당을 맡을 수도 있다. 동시 배정은 한 명만 성공하며 완료는 담당자 또는 관리자만 가능하다.
작업 상세에는 각 단계의 사용자·시각·처리 경로(Slack/웹)가 남는다. 이 상태는 실제 장애 복구 판정과 별개다.
기존 발송 완료 알림도 웹에서 처리할 수 있으며 외부 연결 없이 사용할 수 있다.

Slack 메시지 안에서도 처리하려면 관리자가 다음 항목을 한 번 설정한다.

1. 위의 발송 연결을 저장한 뒤 **Slack 버튼과 담당자 연결 설정**을 펼친다.
2. Slack 앱의 **Basic Information**에서 앱 ID와 Signing Secret을 확인하고,
   발송 대상 워크스페이스 ID와 채널 ID를 입력한다. Signing Secret은 Webhook URL과 다른 값이다.
3. Slack 프로필 메뉴에서 확인한 사용자 ID를 해당 고객사의 OpsLens MSP 계정에 연결한다.
   등록하지 않은 사용자와 다른 고객사의 엔지니어는 버튼으로 처리할 수 없다.
4. 앱의 **Interactivity & Shortcuts**를 켜고 Request URL을
   `https://<운영 서버 주소>/api/slack/interactions`로 저장한다.
   공개 HTTPS 주소가 필요하며 localhost 주소로는 Slack이 콜백을 보낼 수 없다.
   프록시는 원본 form body와 `X-Slack-Signature`, `X-Slack-Request-Timestamp` 헤더를 그대로 전달해야 한다.
5. **Slack 처리 버튼 사용**을 켜서 저장한 후 새 알림을 검토·승인한다. 이후 새로 발송하는 메시지에 버튼이 붙는다.
   [Slack 공식 상호작용 설정 안내](https://docs.slack.dev/interactivity/handling-user-interaction/).

Signing Secret과 응답 URL은 암호화한다. 요청은 원문 HMAC 서명·5분 유효 시각·앱·워크스페이스·채널·사용자 권한을 검사하며,
반복 요청은 중복 처리하지 않는다. 담당 기록 저장 후 빠르게 응답하고 메시지 표시는 별도 대기열로 갱신한다.
발송/추적 설정을 바꾸면 기존 메시지의 버튼은 무효화되므로 웹에서 처리한다.

Slack 버튼 처리는 원래 메시지를 갱신하며, 웹에서 바꾼 상태는 Slack 메시지에 자동 반영하지 않는다.
메시지 갱신 실패나 Slack 응답 URL 만료 시에도 OpsLens의 처리 기록은 보존된다.
콜백 시 받은 응답 URL은 25분 이내에만 사용하고 429 응답에 한해 최대 3회 시도한다.
이 기능은 개인 DM·자동 재촉·미응답 재배정까지 수행하지 않는다.

관련 API: 관리자 `GET/PUT /api/tenants/{tenantId}/slack-tracking`,
MSP `GET /api/incidents/{incidentId}/slack-tracking`,
`POST /api/incidents/{incidentId}/slack-deliveries/{deliveryId}/tracking` (`ACK`, `START`, `COMPLETE`).
외부 콜백 `POST /api/slack/interactions`만 사용자 JWT 대신 Slack 서명으로 인증한다.

### 정리와 전체 초기화

```bash
scripts/cluster-down.sh                                   # 고객사 클러스터 삭제
docker compose --profile tools down -v --remove-orphans   # 컨테이너와 볼륨(DB·로그) 삭제
```

처음부터 다시 구성하려면 위 두 줄 뒤에 2~4단계를 다시 실행한다.

## E2E 테스트

장애 주입부터 알림, Agent 진단, 승인, 조치 반영, 회복 확인, 보고서·공지까지 실제로 돌리고 판정한다. 환경 세팅 2~4단계가 끝난 상태에서 실행한다.

```bash
python3 scripts/e2e.py                    # 전체 15개 (약 30~40분)
python3 scripts/e2e.py --quick            # PR이 없는 빠른 확인: guard a-hang a-dbslow b-surge
python3 scripts/e2e.py b-hang a-dbslow    # 고른 시나리오만
python3 scripts/e2e.py --list             # 시나리오 목록
```

| 묶음 | 시나리오 | 기대 결과 |
|---|---|---|
| 공통 | `isolation` `question` `no-issue` `documents` | 고객사 격리, 계약 문서 질문(RAG), 장애가 없을 때 "이상 없음", 근거 문서 업로드·색인·되돌리기 |
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

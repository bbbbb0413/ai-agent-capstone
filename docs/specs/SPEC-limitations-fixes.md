# Spec: 알려진 한계 해소

- 상태: **진행 중** (2026-10-23). 1·2·3단계 완료. 4단계 구현, A사 E2E 남음
- 근거: [기획안](../OpsLens-기획안.md) 14.1 알려진 한계, [SPEC-review-fixes](SPEC-review-fixes.md)
- 원칙: 단계마다 테스트를 붙이고 따로 커밋한다. 작고 위험이 낮은 단계부터 한다. 각 단계가 끝나면 14.1의 해당 행을 지우거나 고친다.

## 1. 목표와 순서

| 단계 | 해소할 한계 | 크기 | 확인 |
|---|---|---|---|
| 1 | 조항별 조치 규칙이 세 곳에 있다 | 소 | agent 단위 테스트 |
| 2 | CSRF 토큰이 없다 | 소 | backend·frontend 테스트, E2E `--quick` |
| 3 | 내부 API 토큰이 하나다 | 중~대 | 단위 테스트, 격리 테스트, E2E `--quick` + `isolation` |
| 4 | 관측 쿼리가 데모 앱 전용이고 회복 확인이 서비스를 가리지 않는다 | 중~대 | 단위 테스트, **전체 E2E 14개** |

4단계는 모든 진단 경로를 바꾸므로 마지막에 한다. 일정이 모자라면 4단계는 한계로 남기고 1~3단계만 한다.

## 2. 1단계: 판정표와 런북·로그 신호 일치 테스트

**지금:** `test_runbook_clauses.py`가 런북과 `action_guards.CLAUSE_ACTIONS`를 비교한다. `prompts/propose.md`의 장애 형태 판정표는 비교하지 않는다.

**바꿀 것:** 판정표를 읽어 다음을 확인하는 테스트를 더한다.

- 판정표에 나오는 조항(RB-STD-0x)이 모두 런북에 있다
- 판정표에 나오는 로그 신호 이름(`connection_pool_exhausted` 등)이 모두 `signals.LOG_SIGNALS`에 있고, `LOG_SIGNALS`의 신호는 판정표에 한 번 이상 나온다

프롬프트를 런북에서 자동으로 만들지는 않는다. 프롬프트는 문장을 다듬으며 E2E로 맞춰 온 것이라, 생성으로 바꾸면 모델 동작이 바뀐다.

**완료 기준:** 판정표에서 조항이나 신호 이름을 하나 바꾸면 테스트가 실패한다(뮤테이션으로 확인).

**결과 (완료):** `tests/test_runbook_clauses.py`의 `JudgmentTableTests`. 판정표 조항 = 런북 조항 − RB-STD-06, 판정표 신호 = `signals.LOG_SIGNALS`. 조항 번호 변경, 신호 이름 변경, 행 삭제가 모두 실패로 잡혔다.

## 3. 2단계: CSRF 토큰

**지금:** 화면 인증은 HttpOnly·SameSite=Strict 쿠키이고 CSRF는 꺼 두었다 (`SecurityConfig` TODO).

**바꿀 것:**

- backend: `/api/**` 체인에 `CookieCsrfTokenRepository`(읽을 수 있는 `XSRF-TOKEN` 쿠키)를 켠다. `Authorization: Bearer` 헤더로 온 요청은 쿠키를 쓰지 않으므로 검사에서 뺀다. 로그인 요청은 뺀다.
- frontend: `api.js`가 GET이 아닌 요청에 `XSRF-TOKEN` 쿠키 값을 `X-XSRF-TOKEN` 헤더로 붙인다.
- E2E: `scripts/e2e.py`는 쿠키로 로그인한다. 같은 방식으로 헤더를 붙인다.

**완료 기준:**

- 토큰 없이 쿠키만으로 보낸 변경 요청은 403, 토큰을 붙이면 성공 (backend 테스트)
- Bearer 헤더 요청과 로그인은 토큰 없이 된다
- 프런트 단위 테스트, E2E `--quick` 통과

**결과 (완료):**

- Spring Security 6.5에는 `csrf.spa()`가 없어 같은 구성을 직접 했다: `CookieCsrfTokenRepository`(읽을 수 있는 쿠키, SameSite=Strict)와 요청마다 토큰을 읽는 `EagerCsrfTokenHandler`. Boot 4로 올리는 것은 범위에 비해 커서 하지 않았다.
- **찾은 함정:** 리소스 서버는 Bearer 토큰이 있는 요청을 CSRF 검사에서 뺀다. 그 판단에 쿠키에서 토큰을 읽는 `BearerTokenResolver`를 쓰고 있어서 쿠키 요청까지 검사에서 빠졌다. `CookieBearerTokenResolver`를 `CookieTokenFilter`로 바꿔 CSRF 필터 **뒤**에서 쿠키를 `Authorization` 헤더로 옮긴다. 필터를 CSRF 앞으로 옮기면 테스트가 실패한다.
- spring-security-test의 `jwt()`는 CSRF 필터를 건너뛰게 만들어 테스트에 쓰지 않는다. 로그인 JWT를 쿠키로 싣는다 (`CsrfProtectionTest`).
- 실제 서버: 쿠키만 보낸 변경 요청 403, 토큰 헤더를 붙이면 200, 조회 200.

## 4. 3단계: 작업별 서명 토큰과 서비스별 토큰

**지금:** `SERVICE_TOKEN` 하나가 일곱 가지 호출에 쓰인다. backend `/internal/tenants/**`와 rag-mcp는 `X-Tenant-Id` 헤더의 고객사를 믿는다. 이 토큰을 가진 서비스는 아무 고객사나 헤더에 넣을 수 있다. Alertmanager만 `ALERT_WEBHOOK_TOKEN`을 따로 쓴다.

| 호출 | 보내는 곳 | 받는 곳 |
|---|---|---|
| backend → agent (작업 실행·재개) | `AgentClient` | agent `main.require_service_token` |
| agent → backend (진행 이벤트, 결과) | agent `backend.py` | backend `ServiceTokenFilter` |
| agent → MCP 서버 (도구 호출) | agent `toolbox.load_tools` | business-mcp·rag-mcp 미들웨어, mcp-grafana `MCP_GRAFANA_SERVER_TOKEN` |
| business-mcp → backend (업무 API) | business-mcp `_call` | backend `ServiceTokenFilter` |
| postmortem-indexer → backend (회고 읽기) | `indexer/postmortems.py` | backend `ServiceTokenFilter` |
| ArgoCD 알림 → backend (동기화 완료) | `argocd-notifications-secret` (`cluster-up.sh`) | backend `ServiceTokenFilter` |
| 장애 주입 → 데모 앱 관리 API | `inject.py` (파드 안 환경변수) | demo-app `admin-token` (`demo-app-admin` 시크릿, `cluster-up.sh`) |

### 4.1 토큰을 어디서 받나: 실행할 때 backend에서

작업 토큰을 작업 메시지(Kafka)에 싣지 않는다. 실어 보내면 경로마다 문제가 생긴다.

| 경로 | 메시지에 실으면 |
|---|---|
| 재시도 토픽 | 원래 메시지를 그대로 다시 보내 처음 토큰이 따라간다. 대기가 길어지면 만료된다 |
| 소비 지연 (agent가 오래 죽어 있음) | 꺼낼 때 이미 만료됐을 수 있다 |
| DLT (`tasks.dlt`) | 토큰이 Kafka 보관 기간 동안 브로커에 남는다 |
| DLT 재처리 | 만료된 토큰이라 다시 넣어도 실행할 수 없다 |
| 복구 (다른 파드가 checkpoint에서 이어 감) | 메시지가 없어 토큰도 없다 |

만료된 토큰으로 실행하면 더 나쁘다. MCP 도구 오류는 예외가 아니라 도구 결과로 모델에 돌아가므로, 조회가 모두 401이어도 "원인 미확정" 보고서를 쓰고 DONE으로 끝날 수 있다.

그래서 agent가 **실행 임대를 잡은 직후** backend에 토큰을 요청한다. 실행·재시도·복구·재개가 모두 같은 경로를 탄다.

### 4.2 바꿀 것

1. **토큰 발급 API:** `POST /internal/tasks/{id}/token` (agent 전용 서비스 토큰으로 인증).
   - 작업 상태가 QUEUED·RUNNING일 때만 발급한다. DONE·FAILED·승인 대기 중인 작업에는 409. agent는 승인·반영을 기다리는 동안 실행하지 않고, 재개 요청은 backend가 상태를 RUNNING으로 바꾼 뒤 온다.
   - 담는 값: `tenant_id`(backend가 그 작업의 DB 값으로 정한다. 메시지나 헤더를 믿지 않는다), `task_id`, 만료 시각. JWT(공개키 서명, 아래 2항).
   - 유효 15분. 실행 한 구간(최대 약 4분: 한도 120초 + 회복 확인 대기)보다 넉넉하다. 실행·재개를 시작할 때마다 새로 받는다.
2. **서명 키: 공개키 방식.** HMAC은 검증 키와 발급 키가 같다. rag-mcp가 토큰을 검증하려면 그 키를 가져야 하고, rag-mcp가 뚫리면 아무 고객사의 토큰이나 만들 수 있다.
   - backend가 기동할 때 서명 키 쌍(Ed25519, 안 되면 RSA)을 만들고 공개키를 `GET /internal/.well-known/jwks.json`으로 내보낸다. 발급은 backend가 이미 쓰는 nimbus로 한다.
   - rag-mcp는 공개키로 검증만 한다. 키를 받아 캐시하고, 모르는 키 ID(`kid`)가 오면 다시 받는다.
   - 새 비밀 환경변수가 필요 없다.
   - 대가: backend를 다시 시작하면 키가 바뀌어 실행 중이던 작업의 토큰이 무효가 된다. agent는 실행 구간마다 새로 받으므로 다음 구간부터 정상이다. 진행 중이던 구간의 도구 호출은 실패할 수 있다. 키를 유지하려면 키 파일을 볼륨에 둔다(범위 밖).
3. **agent:**
   - `runner._drive_claimed`가 도구를 불러오기 전에 토큰을 받는다. 받지 못하면(409, 연결 실패) Graph를 돌리지 않는다.
     - 409(끝난 작업): 실행하지 않고 건너뛴다. 중복·늦게 온 메시지와 DLT에서 다시 꺼낸 메시지가 여기서 멈춘다.
     - 연결 실패: 지금처럼 예외로 올려 Kafka 재시도 경로를 탄다.
   - MCP 연결 헤더 `X-Task-Token`으로 넘긴다. business-mcp는 backend로 그대로 전달한다.
4. **고객사는 토큰에서 정한다:** backend의 `/internal/tenants/{tenantId}/**`와 rag-mcp는 토큰을 검증하고 그 안의 고객사를 쓴다. `X-Tenant-Id`가 토큰과 다르면 지금처럼 403과 감사 기록(`TENANT_ISOLATION_BLOCKED`)을 남긴다.
5. **서비스별 토큰:** `SERVICE_TOKEN` 하나를 호출마다 나눈다. 토큰 하나가 새도 다른 호출은 막힌다. 작업 토큰은 고객사를 정하고, 서비스 토큰은 호출한 서비스를 확인한다.

   | `.env` 변수 | 보내는 쪽 (설정 위치) | 받아서 검사하는 쪽 (설정 위치) | backend에서 허용하는 경로 |
   |---|---|---|---|
   | `BACKEND_AGENT_TOKEN` | backend `application.yml` → `AgentClient` | agent `config.py` → `main.require_service_token` | (agent `/agent/tasks/**`) |
   | `AGENT_BACKEND_TOKEN` | agent `config.py` → `backend.py` | backend `ServiceTokenFilter` | `/internal/tasks/**` (이벤트, 결과, 토큰 발급) |
   | `AGENT_MCP_TOKEN` | agent `config.py` → `toolbox.load_tools` | business-mcp·rag-mcp 미들웨어, mcp-grafana `MCP_GRAFANA_SERVER_TOKEN` | (MCP 서버) |
   | `MCP_BACKEND_TOKEN` | business-mcp `server.py` `_call` | backend `ServiceTokenFilter` | `/internal/tenants/**` (작업 토큰도 있어야 한다), `/internal/.well-known/jwks.json` |
   | `INDEXER_BACKEND_TOKEN` | postmortem-indexer `postmortems.py` | backend `ServiceTokenFilter` | `/internal/tenants/*/postmortems`만 (고객사 전체를 도는 배치라 작업 토큰 없음) |
   | `GITOPS_WEBHOOK_TOKEN` | ArgoCD `argocd-notifications-secret` (`cluster-up.sh`가 넣는다) | backend `ServiceTokenFilter` | `/internal/gitops/sync-events`만 |

   rag-mcp는 공개키만 받으면 되므로 backend 호출 토큰이 필요 없다. jwks는 공개 정보라 `MCP_BACKEND_TOKEN` 없이 열어도 되지만, 내부 경로 규칙을 하나로 두려고 토큰을 요구한다. rag-mcp에는 `MCP_BACKEND_TOKEN`을 넣는다.

   함께 고칠 곳:
   - `docker-compose.yml`: 공통 앵커 `x-service-token`(backend, agent, business-mcp, rag-mcp, postmortem-indexer)을 없애고 서비스마다 필요한 변수만 넣는다. mcp-grafana는 `MCP_GRAFANA_SERVER_TOKEN: ${AGENT_MCP_TOKEN}`.
   - backend: `OpsLensProperties`에 토큰 필드를 더하고, `ServiceTokenFilter`의 경로 하나짜리 분기(`/internal/alerts`)를 "경로 → 허용 토큰" 표로 바꾼다.
   - **데모 앱 관리 토큰은 분리한다:** 고객사 쪽 비밀이라 OpsLens 내부 토큰과 섞지 않는다. `.env`의 `DEMO_ADMIN_TOKEN`을 `cluster-up.sh`가 `demo-app-admin` 시크릿에 넣는다. 파드 안의 환경변수 이름은 그대로 두므로 `gitops/tenants/*`, demo-app, `inject.py`는 고치지 않는다.
   - `scripts/e2e.py`: `guard`·`isolation`은 backend 컨테이너 안에서 `$SERVICE_TOKEN`으로 `/internal/tenants/**`를 직접 부른다. `MCP_BACKEND_TOKEN`과 작업 토큰을 쓰도록 바꾼다(테스트용 작업을 만들어 토큰을 받는다).
   - `.env.example`, README: 변수 목록과 설명.
   - **하위 호환은 두지 않는다 (2026-10-23 결정):** `SERVICE_TOKEN`을 없앤다. 새 변수는 compose에서 `${VAR:?}`로 필수로 두어, 비어 있으면 기동하지 않는다. 같은 값을 두 변수에 넣는 실수를 막으려고 backend는 기동할 때 서비스 토큰 6개가 서로 다른지 확인하고, 같으면 기동을 멈춘다.
   - `scripts/cluster-up.sh`: `.env`의 `SERVICE_TOKEN` 대신 `DEMO_ADMIN_TOKEN`(demo-app-admin 시크릿)과 `GITOPS_WEBHOOK_TOKEN`(argocd-notifications-secret)을 쓴다.
   - **올리는 순서:** 사용자가 `.env`에 새 변수 7개를 채운다 → `docker compose up -d --build` → `scripts/cluster-up.sh`를 다시 실행해 클러스터 시크릿을 새 값으로 바꾼다. README의 환경 세팅 절에 적는다.
6. **실패한 작업 다시 실행 (DLT 재처리 절차):** DLT 메시지를 직접 다시 넣지 않는다. 관리자가 작업 상세의 버튼이나 API로 다시 실행을 요청한다.
   - **API:** `POST /api/admin/tasks/{id}/retry` (MSP 관리자만). FAILED 작업만 받고, 그 밖의 상태는 409. 같은 작업을 두 번 다시 실행하면 409(이미 다시 실행한 작업)다.
   - **동작:** 같은 고객사·인시던트·유형·질문으로 **새 작업**을 만들고 Outbox로 보낸다. 원래 작업은 FAILED로 남는다. 새 작업에 원래 작업 ID(`retry_of`)를 남기고 감사 로그(`TASK_RETRIED`, 요청자, 두 작업 ID)를 쓴다. 첨부 분석 작업(ATTACHMENT)은 원래 작업의 첨부를 새 작업에 연결한다(구현 때 `TaskAttachments` 저장 구조를 확인한다).
   - **DB (V16 마이그레이션):** `ai_tasks.retry_of UUID NULL REFERENCES ai_tasks(id)`, 같은 원래 작업을 두 번 다시 실행하지 못하게 `retry_of`에 유일 인덱스.
   - **새 작업으로 하는 이유:** 원래 작업의 checkpoint와 실행 시간 예산(`task_budget`)은 이미 한도를 넘었거나 실패 지점에 있다. 이어 가면 바로 다시 한도에 걸린다.
   - **화면 (`front/src/views/TaskView.vue`):**
     - MSP 관리자에게만, FAILED 작업에만 "다시 실행" 버튼을 보인다. 이미 다시 실행한 작업이면 버튼 대신 "다시 실행한 작업" 링크를 보인다.
     - 누르면 확인 창("같은 내용으로 새 작업을 만든다. 원래 작업은 실패로 남는다")을 띄우고, 성공하면 새 작업 화면으로 이동한다.
     - 새 작업 화면에는 "작업 #… 다시 실행" 표시와 원래 작업 링크를 보인다.
     - 작업 조회 응답에 `retryOf`와 `retriedBy`(이 작업을 다시 실행한 새 작업 ID)를 더한다.
   - **DLT:** 실패 기록과 원인 확인용으로 남는다. 메시지에 비밀 값이 없으므로 보관해도 된다.
7. mcp-grafana는 지금처럼 데이터소스 강제(`harness.enforce_tenant_datasource`)와 prom-label-proxy로 격리한다. 작업 토큰은 쓰지 않는다(외부 이미지라 헤더를 검사할 수 없다).

### 4.3 경로별 동작

| 경로 | 동작 |
|---|---|
| 정상 실행, 재시도 토픽, 소비 지연 | 꺼내 실행할 때 새 토큰을 받는다. 만료 문제가 없다 |
| DLT로 간 작업 | 이미 FAILED라 토큰을 받지 못한다. 메시지가 다시 와도 실행되지 않는다 |
| 다시 실행 | 관리자 요청으로 새 작업을 만든다 (4.2의 6) |
| 복구, 재개 | 같은 발급 API로 받는다 |

### 4.4 남는 한계

- agent 서비스 토큰(`AGENT_BACKEND_TOKEN`)이 새면, 그 순간 실행 중인 작업이 있는 고객사의 토큰은 받아 갈 수 있다. 지금(아무 고객사나 언제든)보다 범위가 크게 줄고, MCP 서버 하나가 뚫려도 다른 고객사에는 닿지 않는다.
- postmortem-indexer 토큰이 새면 모든 고객사의 회고(종료된 장애 보고서)를 읽을 수 있다. 읽기 전용이고 경로 하나로 묶인다.
- backend 재시작 때 서명 키가 바뀐다 (4.2의 2).

### 4.5 정할 것 (승인 때 확인)

- (결정) 새 환경변수 7개: 서비스 토큰 6개(4.2의 5 표)와 `DEMO_ADMIN_TOKEN`. 하위 호환 없이 필수. 작업 토큰 서명 키는 backend가 만들어 환경변수가 없다. `.env`는 사용자가 채운다.
- (결정) 다시 실행: 관리자용 버튼까지 만든다 (4.2의 6).

### 4.6 완료 기준

- 다른 고객사의 작업 토큰으로 조회하면 403, 위조·만료 토큰이면 401 (backend·rag-mcp 테스트)
- DONE·FAILED 작업에는 토큰이 발급되지 않고, agent는 그 작업을 실행하지 않는다 (단위 테스트)
- MCP 서버의 토큰으로는 토큰 발급 API와 agent API(`/agent/tasks/**`)를 부를 수 없다. 서비스 토큰마다 허용 경로 밖은 401 (backend 테스트)
- rag-mcp는 공개키로 검증하고, 다른 키로 서명한 토큰은 거부한다
- 새 변수가 하나라도 비어 있으면 compose가 기동하지 않고, 서비스 토큰 둘이 같으면 backend가 기동하지 않는다
- 코드·compose·스크립트에 `SERVICE_TOKEN`이 남지 않는다
- 재시도·복구·재개 경로가 새 토큰으로 이어 간다 (단위 테스트)
- 다시 실행하면 새 작업이 생기고 원래 작업과 연결된다. 관리자가 아니거나 FAILED가 아니거나 이미 다시 실행한 작업이면 거부된다 (backend 테스트)
- 화면: 관리자·FAILED 작업에만 버튼이 보이고, 누르면 새 작업 화면으로 간다 (프런트 테스트)
- Kafka 메시지(`tasks.*`, `tasks.dlt`)에 토큰이 없다
- E2E `--quick`과 `isolation` 통과

### 4.7 결과 (완료)

- backend: `TaskTokens`(RS256, 기동 때 키 생성, 15분, QUEUED·RUNNING만 발급)와 `TaskTokenController`(`POST /internal/tasks/{id}/token`, `GET /internal/.well-known/jwks.json`). `InternalTenantController`는 `X-Tenant-Id` 대신 `X-Task-Token`의 고객사로 경로를 검사하고, 다르면 403과 감사 로그 `TENANT_ISOLATION_BLOCKED`(작업 ID 포함)를 남긴다.
- `ServiceTokenFilter`는 경로별로 받는 토큰이 하나다. 표에 없는 `/internal/**` 경로는 401. `InternalTokenCheck`가 기동 때 토큰 6개가 모두 있고 서로 다른지 본다 (오류 메시지에는 변수 이름만 쓴다).
- agent는 lease를 잡은 뒤 토큰을 받는다. 409면 `NOT_ACTIVE`로 실행하지 않는다. business-mcp·rag-mcp는 `X-Task-Token`이 없으면 거부하고, rag-mcp는 JWKS 공개키로 검증한다(`task_token.py`, PyJWT).
- 다시 실행: `V16__task_retry.sql`(`retry_of`, 고유 색인), `POST /api/admin/tasks/{id}/retry`, 첨부 복사, 감사 `TASK_RETRIED`, 작업 화면 `TaskRetry.vue`.
- 테스트: backend 전체, agent 96건, rag-mcp·business-mcp·indexer, 프런트 31건 통과.
- 실제 서버: 토큰마다 허용 경로 밖은 401(agent 토큰 → 고객사 API, MCP 토큰 → 토큰 발급, indexer 토큰 → 고객사 API, gitops 토큰 → 작업 이벤트). 작업 토큰 없는 MCP 요청 401. JWKS에는 공개키만 있다. 다시 실행은 엔지니어 403, 관리자 202(새 작업 DONE, 원래 작업과 연결), 두 번째 409.
- E2E(2026-10-23): `isolation question no-issue guard a-hang a-dbslow a-pool b-surge` 전부 통과. 격리에서 B사 작업 토큰으로 A사 내부 API를 부르면 헤더에 A사를 적어도 403, 작업 토큰이 없으면 401이고, 차단이 감사 로그에 남는다.

## 5. 4단계: 서비스별 관측 정의를 서비스 카탈로그로

**지금:**

- 진단·회복 확인 쿼리, 로그 선택자, 로그 신호 문구가 데모 앱 기준으로 `agent/signals.py`에 있다(`demo_orders_seconds_*`, `{app="demo-app"}`, `Connection is not available` 등).
- 커넥션 풀 크기는 데모 앱 설정 이름 `DEMO_POOL_SIZE`로 읽는다 (`ChangeService`).
- 회복 확인 쿼리에 서비스 조건이 없다. 메트릭에는 `service` 라벨이 있지만(`gitops/cluster/metrics-agent.yaml`) 로그에는 없다(`app`·`tenant`·`level`만, demo-app `logback-spring.xml`).

**바꿀 것:**

1. **카탈로그 (V17 마이그레이션, V16은 3단계):** `services`에 관측 정의를 더한다. 두 데모 서비스는 지금 값으로 채운다.
   - `request_metric`: 요청 히스토그램 이름. 예: `demo_orders_seconds`
   - `error_selector`: 에러 요청을 고르는 라벨 조건. 예: `outcome="error"`
   - `log_selector`: Loki 선택자. 예: `{app="demo-app", service="order-api"}`
   - `log_signals`: 신호 이름 → 로그 문구 (JSONB). 신호 이름 5개는 런북 조항과 묶여 있어 고정이고, 문구만 서비스마다 다르다
   - `pool_size_env`: 풀 크기 환경변수 이름. 예: `DEMO_POOL_SIZE`
2. **로그에 서비스 라벨:** demo-app 로그에 `service` 라벨을 더한다. 값은 배포 설정의 환경변수로 넘기고, A사 GitOps 설정과 B사 초기 배포를 고친다.
3. **backend:**
   - 서비스 목록(`inventory`)에 `observability`를 싣는다.
   - `ChangeService`는 `pool_size_env`로 풀 크기를 읽는다.
4. **agent (`signals.py`):**
   - 쿼리를 상수가 아니라 서비스의 관측 정의로 만든다. 모든 쿼리에 `service="<서비스>"` 조건을 붙인다.
   - 진단은 인시던트의 서비스, 회복 확인은 조치한 서비스로 거른다.
   - 관측 정의가 없는 서비스는 진단을 원인 미확정으로 끝내고 사유를 남긴다. 데모 기본값으로 대신하지 않는다.
5. **알림 규칙:** `alert-rules.yml`은 이미 `service`별로 묶는다. 메트릭 이름은 그대로 두고, 이 단계에서는 바꾸지 않는다(서비스마다 메트릭 이름이 다르면 규칙 생성이 필요하다. 범위 밖).

**완료 기준:**

- `signals.py`, `action_guards.py`, `ChangeService`에 데모 앱 이름(`demo_orders`, `demo-app`, `DEMO_POOL_SIZE`)이 남지 않는다
- 카탈로그 값으로 만든 쿼리가 지금 쿼리와 같은 결과를 낸다 (단위 테스트: 같은 입력 → 지금과 같은 PromQL·LogQL에 서비스 조건만 더해짐)
- 한 고객사에 서비스를 둘 둔 테스트에서 회복 확인이 조치한 서비스만 본다
- 전체 E2E 14개 통과

**결과:**

- backend: `V17__service_observability.sql`(`request_metric`, `error_selector`, `log_selector`, `log_signals`, `pool_size_env`). `log_selector`는 중괄호 없는 라벨 조건(`app="demo-app"`)이고 `service` 조건은 agent가 붙인다. 서비스 목록에 `observability`(없으면 null)를 싣고, `ChangeService`는 `pool_size_env`로 풀 크기를 읽는다.
- agent: `signals.observability`·`metric_queries`·`log_query`가 관측 정의로 쿼리를 만든다. 진단은 인시던트의 서비스(질문이면 질문 속 서비스나 열린 인시던트가 한 서비스일 때 그 서비스), 회복 확인은 조치한 서비스로 거른다. 관측 정의가 없으면 진단은 원인 미확정, 회복 확인은 조회 없이 NOT_RECOVERED. 첨부 분석의 현재 지표도 같은 방식이다. 진단 프롬프트의 메트릭·로그 이름은 관측 정의를 가리키게 바꿨다.
- 로그 신호 이름 5개는 `signals.LOG_SIGNALS`(이름 → 런북 조항)에 고정이고, 카탈로그에 문구가 없는 신호는 0건이 아니라 결과에서 빠진다.
- demo-app 로그에 `service` 라벨(`SERVICE_NAME`). A사 GitOps 설정과 B사 초기 배포에 넣었다.
- 테스트: backend 전체, agent 104건(`tests/test_signals.py`: 지금 쿼리 + 서비스 조건과 같음, 두 서비스 중 조치한 서비스만 측정, 관측 정의 없음).
- E2E(2026-10-23): `isolation question no-issue b-hang b-latency b-pool b-surge` 통과.
- 남는 것: 알림 규칙, 영향 범위(`ImpactMetrics`), 의존 대상 상태(`DependencyHealth`)는 아직 데모 메트릭 이름을 쓴다 (5번과 같은 이유로 범위 밖).

## 6. 하지 않는 것

- 서비스마다 다른 메트릭 이름에 맞춘 알림 규칙 자동 생성
- 외부 이미지인 mcp-grafana에서 작업 토큰 검증
- 판정표 프롬프트 자동 생성 (1단계 이유)

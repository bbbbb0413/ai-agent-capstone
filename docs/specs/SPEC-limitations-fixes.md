# Spec: 알려진 한계 해소

- 상태: **진행 중** (2026-10-23). 1·2단계 완료. 3·4단계는 승인 전
- 근거: [기획안](../OpsLens-기획안.md) 14.1 알려진 한계, [SPEC-review-fixes](SPEC-review-fixes.md)
- 원칙: 단계마다 테스트를 붙이고 따로 커밋한다. 작고 위험이 낮은 단계부터 한다. 각 단계가 끝나면 14.1의 해당 행을 지우거나 고친다.

## 1. 목표와 순서

| 단계 | 해소할 한계 | 크기 | 확인 |
|---|---|---|---|
| 1 | 조항별 조치 규칙이 세 곳에 있다 | 소 | agent 단위 테스트 |
| 2 | CSRF 토큰이 없다 | 소 | backend·frontend 테스트, E2E `--quick` |
| 3 | 내부 API 토큰이 하나다 | 중 | 단위 테스트, 격리 테스트, E2E `--quick` + `isolation` |
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

**지금:** agent·business-mcp·rag-mcp·mcp-grafana가 같은 `SERVICE_TOKEN`을 쓴다. backend `/internal/tenants/**`와 rag-mcp는 `X-Tenant-Id` 헤더의 고객사를 믿는다. 이 토큰을 가진 서비스는 아무 고객사나 헤더에 넣을 수 있다.

**바꿀 것:**

1. **작업 토큰:** backend가 작업을 만들 때 HMAC 서명 토큰을 발급한다. 담는 값: `tenant_id`, `task_id`, 만료 시각.
   - backend가 agent에 보내는 작업 메시지와 재개 요청에 싣는다.
   - agent는 MCP 연결 헤더 `X-Task-Token`으로 넘긴다. business-mcp는 backend로 그대로 전달한다.
2. **고객사는 토큰에서 정한다:** backend의 `/internal/tenants/{tenantId}/**`와 rag-mcp는 토큰을 검증하고 그 안의 고객사를 쓴다. `X-Tenant-Id`가 토큰과 다르면 지금처럼 403과 감사 기록(`TENANT_ISOLATION_BLOCKED`)을 남긴다.
3. **서비스별 토큰:** `SERVICE_TOKEN` 하나를 호출 방향별로 나눈다.
   - backend → agent
   - agent → backend
   - MCP 서버 → backend
   - agent → MCP 서버

   토큰 하나가 새도 다른 방향의 호출은 막힌다. 작업 토큰은 고객사를 정하고, 서비스 토큰은 호출한 서비스를 확인한다.
4. mcp-grafana는 지금처럼 데이터소스 강제(`harness.enforce_tenant_datasource`)와 prom-label-proxy로 격리한다. 작업 토큰은 쓰지 않는다(외부 이미지라 헤더를 검사할 수 없다).

**정할 것 (승인 때 확인):**

- 토큰 만료: 승인·PR 병합 대기가 길다(병합 기한 30분). 제안: backend가 실행과 재개 때마다 새 토큰(유효 15분)을 발급한다. 복구 경로(agent가 스스로 checkpoint에서 이어 감)에는 새 토큰이 없으므로, agent가 backend 내부 API로 그 작업의 토큰을 다시 받는다.
- 서명 키: 새 환경변수 `TASK_TOKEN_SECRET`. 서비스 토큰도 새 변수가 생긴다. `.env.example`과 README를 고친다.

**완료 기준:**

- 다른 고객사의 작업 토큰으로 조회하면 403, 위조·만료 토큰이면 401 (backend·rag-mcp 테스트)
- MCP 서버의 토큰으로는 agent API(`/agent/tasks/**`)를 부를 수 없다
- 재개와 복구 경로가 새 토큰으로 이어 간다 (단위 테스트)
- E2E `--quick`과 `isolation` 통과

## 5. 4단계: 서비스별 관측 정의를 서비스 카탈로그로

**지금:**

- 진단·회복 확인 쿼리, 로그 선택자, 로그 신호 문구가 데모 앱 기준으로 `agent/signals.py`에 있다(`demo_orders_seconds_*`, `{app="demo-app"}`, `Connection is not available` 등).
- 커넥션 풀 크기는 데모 앱 설정 이름 `DEMO_POOL_SIZE`로 읽는다 (`ChangeService`).
- 회복 확인 쿼리에 서비스 조건이 없다. 메트릭에는 `service` 라벨이 있지만(`gitops/cluster/metrics-agent.yaml`) 로그에는 없다(`app`·`tenant`·`level`만, demo-app `logback-spring.xml`).

**바꿀 것:**

1. **카탈로그 (V16 마이그레이션):** `services`에 관측 정의를 더한다. 두 데모 서비스는 지금 값으로 채운다.
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

## 6. 하지 않는 것

- 서비스마다 다른 메트릭 이름에 맞춘 알림 규칙 자동 생성
- 외부 이미지인 mcp-grafana에서 작업 토큰 검증
- 판정표 프롬프트 자동 생성 (1단계 이유)

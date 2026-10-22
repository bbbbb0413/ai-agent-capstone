# 테스트

| 종류 | 무엇을 확인하나 | 실행 | 걸리는 시간 |
|---|---|---|---|
| 전체 시나리오 E2E | 장애 주입부터 승인·조치·회복 확인·보고서까지 실제 스택에서 | `python3 scripts/e2e.py` | 전체 약 20분 |
| 단위·DB 테스트 | 프론트 타임라인, Agent 도구 결과·분기, backend 소유권·승인·SLA | 아래 2장 | 1분 안팎 |

## 1. 전체 시나리오 E2E (`scripts/e2e.py`)

compose 스택과 kind 클러스터(`scripts/cluster-up.sh`)가 떠 있어야 한다. backend에 `GITHUB_TOKEN`이 있어야 한다 (임시 브랜치, A사 PR).
문서가 추가·수정된 커밋을 받은 뒤에는 indexer를 다시 실행한다. 사전 점검은 공통 표준 런북의 현재 파일 버전이 RAG DB에 있는지 확인한다.

```bash
python3 scripts/e2e.py                 # 전체: 사전 점검 → 공통 묶음 → A사·B사 묶음 동시에 (약 30~40분)
python3 scripts/e2e.py b-hang a-dbslow # 고른 시나리오만 (사전 점검은 항상). 예전 키 a·b·b2도 받는다
python3 scripts/e2e.py --quick         # PR이 없는 빠른 확인: guard a-hang a-dbslow b-surge
python3 scripts/e2e.py --list          # 시나리오 목록
python3 scripts/e2e.py a-pool --no-merge  # A사 PR은 사람이 병합 (병합될 때까지 기다린다)
python3 scripts/e2e.py --serial        # 고객사 묶음을 하나씩 (원인을 좁힐 때)
python3 scripts/e2e.py --auto-approve  # backend 자동 승인 설정을 그대로 쓴다
python3 scripts/e2e.py --cleanup       # 중단된 실행이 남긴 임시 브랜치·설정을 정리한다
```

| 묶음 | 키 | 시나리오 | 기대 결과 |
|---|---|---|---|
| 공통 | `isolation` | 고객사 격리 | B사 담당자·B사 작업·B사 ServiceAccount로 A사 자원 접근 거부, 감사 로그 |
| 공통 | `question` | 문서 질문 | RAG로 계약 조항을 찾아 답하고 출처를 단다 |
| 공통 | `no-issue` | 이상 없음 | 열린 인시던트가 없는 고객사의 일반 질문은 진단·조치 없이 30초 안에 "이상 없음". 서비스명이 있는 증상 제보는 승인 후 P2 인시던트를 등록해 진단으로 연결 |
| A사(끝) | `guard` | backend 방어선 | 내부 API로 직접 요청: A사 HPA 서비스 직접 확장, HPA 최대치 5(안전 상한 4), B사 Docker 서비스 재시작, B사 롤백이 모두 422. 레플리카 불변 |
| A사 | `a-hang` | 파드 멈춤 | 승인 카드에 순차 재시작·무중단 표시, `restart_pods`를 API로 실행(`ROLLING`) → 회복 |
| A사 | `a-latency` | 외부 결제 지연 | 앱 조치 없이 고객 공지 |
| A사 | `a-dbslow` | DB 자체 지연 | `DEPENDENCY`(postgres) 에스컬레이션, 받는 사람 A사 DBA팀, 공지, 앱 조치 없음 |
| A사 | `a-pool` (예전 `a`) | 잘못된 설정 배포 | 롤백 PR → 병합 → ArgoCD 동기화 → 회복, 풀 크기 20 |
| A사 | `a-surge` | 트래픽 급증 +250건/초 | `adjust_autoscaler` 최대 4 PR → 병합 → HPA 최대 4 → 회복. 직접 확장·4 초과 제안 없음 |
| A사 | `a-surge-max` | 트래픽 급증 +400건/초 | 최대 4로 올린 뒤에도 미회복 → `ACTION_BLOCKED`/`STRUCTURE` 에스컬레이션(A사 운영 담당), 공지 |
| B사 | `b-hang` (예전 `b`) | 파드 멈춤 | 승인 카드에 전체 중단 표시, `restart_pods`를 API로 실행 → 회복 |
| B사 | `b-latency` (예전 `b2`) | 외부 결제 지연 | 재시작하지 않고 고객 공지 |
| B사 | `b-pool` | 잘못된 배포 | 롤백이 계약상 불가 → `ACTION_BLOCKED`/`CONTRACT` 에스컬레이션(B사 운영 승인 담당), 공지, 재시작 제안 없음 |
| B사 | `b-surge` | 트래픽 급증 | 확장이 계약상 불가 → `ACTION_BLOCKED`/`CONTRACT` 에스컬레이션, 공지 |

- **실행 순서:** 공통 묶음(장애가 없을 때 돌린다)을 먼저 돌리고, A사 묶음과 B사 묶음을 동시에 돌린다. 한 고객사 안에서는 순서대로 돈다(서비스가 하나라 장애가 섞이고 같은 알림은 인시던트 하나로 합쳐진다). A사 묶음이 가장 길다(확장 시나리오의 PR 병합과 원복 대기).
- **정리:** 시나리오마다 장애를 모두 뺀다(관리 API 장애, 부하 발생기 0·기본 건수, 풀 20, A사 HPA 최대 3 복원 커밋). A사는 HPA가 레플리카 2로 줄고 최대 3이 동기화될 때까지 기다린다.
- **승인:** 스크립트가 제안을 검사해 기대한 조치만 승인하고, 나머지는 거절한다. 잘못된 조치가 실행되지 않게 하기 위해서다. 실행하는 동안 backend 자동 승인을 끄고, 끝나면 되돌린다.
- **판정:** 기대한 조치를 한 번만 제안했는지, 반영 방식, 회복 확인, 보고서·공지, 정리 상태를 본다. 승인 요청은 저장된 이벤트로 판정하므로 자동 승인으로 진행돼도 기준이 같다.
- **장애 사이 간격:** 같은 고객사에서 앞 장애의 인시던트가 닫히고(알림 해제 반영), 정리한 지 75초가 지나야 다음 장애를 주입한다. 인시던트가 열린 채로 같은 알림(예: p95 1초 초과)이 다시 울리면 그 인시던트로 합쳐져 새 작업이 생기지 않는다. Agent는 인시던트 시작 2분 전부터 로그를 보므로, 간격이 짧으면 앞 장애의 로그가 다음 진단 근거에 섞인다.
- **결과:** `.local/e2e/<시각>/` 폴더(git에 올리지 않음): `summary.md`(실행 중에도 갱신), 시나리오별 `<키>.log`·`<키>.md`(판정, 작업 링크, 보고서).
- **임시 브랜치:** 실행 동안 A사 설정 저장소 기준을 `e2e/<시각>` 브랜치로 바꾼다(backend `tenants.gitops_branch`, ArgoCD `targetRevision`). 장애 주입 커밋·롤백 PR·병합이 그 브랜치에서 일어나 `main`에 남지 않고, 실행 중 다른 사람이 `main`에 푸시해도 A사가 다시 동기화되지 않는다. 끝나면(실패·중단 포함) `main`으로 되돌리고 브랜치를 지운다. 남았으면 사전 점검이 잡고 `--cleanup`으로 정리한다. PR 기록은 GitHub에 닫힌 PR로 남는다([SPEC 5.8](specs/SPEC-fault-based-actions.md)).
- 실행이 겹치면 두 번째 실행은 시작하지 않는다.

### 장애 주입 (`scripts/inject.sh`)

E2E 밖에서 손으로 장애를 넣고 뺄 때 쓴다. E2E도 같은 코드(`scripts/inject.py`)를 쓴다.

```bash
scripts/inject.sh tenant-b hang            # 작업 스레드 고갈
scripts/inject.sh tenant-a dbslow          # DB 자체 지연
scripts/inject.sh tenant-b surge           # 트래픽 급증 (부하 발생기 켬)
scripts/inject.sh tenant-a pool            # 잘못된 설정 배포. ArgoCD가 main을 따라가면 --allow-main이 필요하다
scripts/inject.sh tenant-a clear           # 모두 원래대로
```

| 키 | A사 (GITOPS) | B사 (DIRECT) |
|---|---|---|
| `pool` | ArgoCD가 따라가는 브랜치에 풀 5 커밋 | `DEMO_POOL_SIZE=5` 직접 배포 |
| `hang` · `latency` · `dbslow` | 데모 앱 관리 API (`WORKER_HANG`·`PAYMENT_LATENCY`·`DB_SLOW`) | 같음 |
| `surge` | `loadgen-tenant-a` 레플리카 1 (초당 250건) | `loadgen-tenant-b` 레플리카 1 (초당 120건) |
| `clear` | 관리 API 해제, 부하 발생기 0, 풀 크기 20 | 같음 |

`scenario-a.sh`, `scenario-b.sh`는 이 스크립트의 래퍼로 남아 있다.
- 장애 형태 × 고객사 교차 매트릭스로 확장하는 계획: [specs/SPEC-fault-based-actions.md](specs/SPEC-fault-based-actions.md)

## 2. 작업 결과·승인 회귀 검증

### 변경된 동작

- MCP 도구의 오류 상태와 호출 ID를 메시지와 TOOL_END 이벤트에 보존한다. 완료된 작업 안에서도 실패한 도구는 실패로 표시한다.
- 리포트와 요약에 조치 결과, PR 생성, 동기화 상태를 전달한다. 승인은 실행 성공과 구분한다.
- incidentId가 없는 질문은 실행안을 만들지 않고 보고서로 종료한다. 단, 열린 인시던트가 없더라도 서비스명이 분명한 증상 제보는 `business_create_incident` 승인 카드로 P2 인시던트 등록을 제안하고, 승인 뒤에만 기존 진단 흐름으로 연결한다.
- incidentId가 없는 SLA·계약·런북 질문은 장애 진단 대신 RAG 문서 조회로 바로 보내고, 일반 답변 형식으로 필요한 값과 출처만 표시한다.
- 작업 생성과 보고서 저장에서 인시던트 소유 고객사를 검증한다.
- 승인은 DB에서 대상 pending_action과 상태를 비교해 한 요청만 처리한다. 기록을 커밋한 뒤 Agent를 재개하고, 전달 실패 시 같은 승인 카드를 복원한다.
- approvalId로 승인 대상을 식별한다. 단일 Agent 프로세스 내 동시 재개를 직렬화하고, 지난 승인 ID로 새로운 승인 단계를 재개하지 않는다. Agent 다중 인스턴스 배포에는 별도 분산 잠금/실행 멱등성 보장이 필요하다.
- SLA 기한은 A사 P1/P2=15/60분, B사 P1/P2=60/240분으로 계약서와 일치시킨다.
- GITOPS 고객사라도 파드 재시작은 Kubernetes API로 바로 반영된다고 표시한다.
- 조치 승인에는 서비스 목록이 계산한 현재·준비 레플리카, 순차/전체 재시작 방식, 확장 안전 상한을 표시한다. 모델의 추정값은 승인 영향으로 사용하지 않는다.

Harness 시간·턴 한도, 원인 미확정 시 RAG 분기, 시나리오 초기화 동작은 변경하지 않는다.

### 자동 테스트

프론트 타임라인·조치 결과·승인 영향 안내:

```bash
cd front
npm test
```

Agent의 MCP 어댑터 오류 보존, 조치 실패 분기, 문서 질문 라우팅, 리포트 입력, 동시 재개:

```bash
docker compose build agent
docker compose run --rm --no-deps -T \
  -v ./server/python/agent/tests:/tests:ro \
  --entrypoint /app/.venv/bin/python \
  agent -m unittest discover -s /tests -v
```

Backend의 소유권, 관리자 승인, 전달 실패 복원, 자동 승인, SLA, 구조 제약, 에스컬레이션 받는 사람·중복 방지:

```bash
docker build --target build -t opslens-backend-tests server/spring/backend
docker run --rm opslens-backend-tests ./gradlew --no-daemon test
```

DB 통합 테스트는 OPSLENS_TEST_DATABASE_URL을 지정했을 때만 실행한다. 반드시 별도의 폐기 가능한 PostgreSQL을 사용한다. 테스트는 생성한 review_UUID 스키마를 정리한다. 동시 승인 두 건 중 한 건만 성공하는지, 재시도 복원이 새 승인 정보를 덮어쓰지 않는지, 다른 고객사 보고서 수정이 차단되는지 확인한다.

### 로컬 실행 확인

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build backend agent frontend
```

Admin/Engineer/Viewer 로그인과 조회, 로그아웃 후 401, 일반 질문 완료, 승인·거절, 도구 오류 표시를 확인한다. 실제 재시작·롤백·외부 공지 없이도 실패 응답과 승인 거절 경로는 검증할 수 있다.

실제 고객사 장애 주입·조치·복구 검증에는 별도 kind 환경과 고객사 접속 정보가 필요하다. 단위/DB 테스트 통과만으로 해당 시나리오 전체 성공을 의미하지 않는다.

## 자동 회고 색인과 검색

- Backend: `PostmortemControllerTest`는 고객사 헤더 불일치 거부를 검사한다.
  `PostmortemRepositoryDatabaseTest`는 종료·완료 보고서만 조회, 고객사 격리, 100건 페이지와 늦게 도착한 보고서를 검사한다.
- Indexer: `tests/test_postmortems.py`는 API 소유권·상태 검증, 전체 페이지 순회, 출처·미검토 표시,
  긴 보고서 분할과 버전 변경을 검사한다. `test_storage_database.py`는 중복 임베딩 방지,
  수정 교체, 임베딩 실패 시 기존 색인 보존을 실제 DB에서 검사한다.
- RAG: `tests/test_postmortem_database.py`는 색인기의 실제 저장 함수를 거쳐 검색·원문 조회를 실행한다.
  다른 고객사·현재 장애 제외와 런북 검색 분리를 검사한다. 외부 임베딩 API만 대역이다.
- Agent: `tests/test_history.py`는 첫 진단에 과거 사례 전달, 잘못된 검색 결과 제외,
  검색 실패 시 계속 진행, 회고가 런북을 밀어내지 않는 동작을 검사한다.

각 Python 서비스 디렉터리에서 `uv sync --frozen` 후 실행한다:

```bash
uv run python -m unittest discover -s tests -v
```

DB 테스트는 **별도 폐기 가능한 PostgreSQL + vector 확장**에 연결해야 한다. Python은
`OPSLENS_TEST_DSN=postgresql://postgres@127.0.0.1:<테스트 포트>/postgres`, Java는
`OPSLENS_TEST_DATABASE_URL=jdbc:postgresql://127.0.0.1:<테스트 포트>/postgres`를 설정한다.
테스트마다 임시 스키마를 만들고 삭제한다. 설정하지 않으면 DB 테스트는 건너뛴다.

2026-10-22 검증: Agent 59개, Indexer 8개(DB 포함), RAG 2개(DB 포함), frontend 10개 통과.
Backend 단위·DB 테스트 41개 통과(총 120개). 실제 OpenAI 임베딩·LLM, 운영 compose, kind 장애 주입은 실행하지 않았다.


## 관리자 설정과 검토 후 Slack 발송 (2026-10-22)

`SlackSafetyTest`는 Webhook 호스트 제한, 암호화·고객사 바인딩, 관리자 쓰기 권한,
설정 응답의 비밀 제외, 429·실패·결과 불명확 구분, plain_text 전송을 검사한다.
`SlackDeliveryDatabaseTest`는 실제 V13 마이그레이션과 PostgreSQL을 사용한다.
설정만 저장했을 때 미발송, 검토한 문안 발송, 중복 승인·동시 워커, 고객사·역할 격리,
설정 변경 시 취소, 429 대기, 응답 유실 시 자동 재전송 금지를 검사한다. Slack HTTP 호출은 대역이다.
기존과 같은 `OPSLENS_TEST_DATABASE_URL`을 설정하고 backend에서 `./gradlew test`를 실행한다.

Frontend는 `npm test`와 `npm run build`로 검증한다. `slack.test.js`는 완료·결과 불명확 메시지의
재발송 차단과 승인/발송 상태 구분을 검사한다. 실제 화면에서는 관리자에게 설정 탭이 보이고,
자동 승인이 허용되지 않는 환경에서도 Slack 설정을 사용할 수 있는지 확인한다.
실제 연결 정보를 넣지 않은 테스트 환경에서는 외부 Slack 발송을 누르지 않는다.

검증 결과: backend 52개(DB 포함), frontend 12개 통과, frontend 배포 빌드 통과.
임시 DB에 V1~V13 전체 마이그레이션 및 관리자 설정 저장 API, 엔지니어 설정 변경 403을 확인했다.
실제 Slack Incoming Webhook을 등록한 뒤 웹 화면에서 테스트 문안을 수정·승인했다.
Backend 발송 상태 `SENT`와 Slack 채널의 동일 메시지 수신을 모두 확인했다(테스트 1건).
실제 연결 정보와 로컬 테스트 DB·스크린샷은 저장소에 포함하지 않는다.

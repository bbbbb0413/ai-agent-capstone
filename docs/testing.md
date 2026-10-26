# 테스트

## 검증 결과 (2026-10-26)

**전체 E2E 18개 시나리오를 동일 환경에서 한 번에 실행해 18/18 통과했다.** 실행 시각은 2026-10-26 10:10:03~10:20:17 KST(약 10분 14초)이며 테스트 대상 소스는 `1e3be40`이다. 과거 실행 결과를 합산한 수치가 아니다.

- 시나리오 세부 검사 **148/148**, 사전 점검 **11/11** 통과(별도 집계).
- 전체에 포함된 복합 장애 3개는 **3/3**, 세부 검사 **26/26** 통과.
- 이전에 실패했던 `b-hang-latency`의 공지는 이번 실행에서 정상 저장됐다. 제품 코드는 수정하지 않았으므로 과거 `service:null` 인자 결함의 근본적인 수정 완료를 뜻하지 않는다.
- 기존 자동 테스트 **340개**(Agent 130·Indexer 35·RAG 8·Backend 129·Frontend 38)는 별도 실행 결과다. 이번 E2E에서 다시 실행하지 않았으며 148개와 합산하지 않는다.
- p95 결측·GitOps revision/health·최종 제안 검사·문서 실패 단계 UI 등 별도 결함 검사의 미해결 상태는 이번 E2E 통과만으로 변경하지 않는다.

[전체 18개 판정·실행 환경·재현 방법·로그 보기](test-results/2026-10-26/README.md)

[별도 자동 테스트 340개·인수조건·미해결 결함의 실행 증거](test-results/2026-10-26/supplemental/README.md)

| 종류 | 무엇을 확인하나 | 실행 | 걸리는 시간 |
|---|---|---|---|
| 전체 시나리오 E2E | 장애 주입부터 승인·조치·회복 확인·보고서까지 실제 스택에서 | `python3 scripts/e2e.py` | 전체 약 10~15분 |
| 단위·DB 테스트 | 프론트 타임라인, Agent 도구 결과·분기, backend 소유권·승인·SLA | 아래 2장 | 1분 안팎 |

## 1. 전체 시나리오 E2E (`scripts/e2e.py`)

compose 스택과 kind 클러스터(`scripts/cluster-up.sh`)가 떠 있어야 한다. backend에 `GITHUB_TOKEN`이 있어야 한다 (임시 브랜치, A사 PR).
문서가 추가·수정된 커밋을 받은 뒤에는 indexer를 다시 실행한다. 사전 점검은 공통 표준 런북의 현재 파일 버전이 RAG DB에 있는지 확인한다.

```bash
python3 scripts/e2e.py                 # 전체: 사전 점검 → 공통 묶음 → 서비스별 5개 묶음 동시에 (이번 실행 약 10분)
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
| 공통 | `documents` | 근거 문서 | 담당 엔지니어가 B사 SLA 계약서 새 버전(docx)을 올리면 30초 안에 색인되고 B사 작업의 문서 검색 1위에 제목 경로와 함께 나온다. 되돌리면 빠진다. 엔지니어의 공통 문서 업로드와 고객사 담당자의 문서 API는 403. 조항 번호를 뺀 공통 표준 런북 PDF는 VALIDATE 실패이고 검색은 이전 버전 그대로 |
| 공통 | `no-issue` | 이상 없음 | 열린 인시던트가 없는 고객사의 일반 질문은 진단·조치 없이 30초 안에 "이상 없음". 서비스명이 있는 증상 제보는 승인 후 P2 인시던트를 등록해 진단으로 연결 |
| A1 order-api | `guard` | backend 방어선 | 내부 API로 직접 요청: A사 HPA 서비스 직접 확장, HPA 최대치 5(안전 상한 4), B사 Docker 서비스 재시작, B사 롤백이 모두 422. 레플리카 불변 |
| A1 order-api | `a-surge` | 트래픽 급증 +250건/초 | `adjust_autoscaler` 최대 4 PR → 병합 → HPA 최대 4 → 회복. 직접 확장·4 초과 제안 없음 |
| A1 order-api | `a-surge-max` | 트래픽 급증 +400건/초 | 최대 4로 올린 뒤에도 미회복 → `ACTION_BLOCKED`/`STRUCTURE` 에스컬레이션(A사 운영 담당), 공지 |
| A2 cart-api | `a-pool` (예전 `a`) | 잘못된 설정 배포 | 롤백 PR → 병합 → ArgoCD 동기화 → 회복, 풀 크기 20 |
| A2 cart-api | `a-dbslow-backlog` | DB 지연 + 트래픽 급증 (겹친 장애) | 진단이 받은 DB p95 비율이 0.5 이상이면 대기열을 DB 지연의 증상으로 보고 DBA 에스컬레이션만, 미만이면 HPA 최대치 4 PR → 다시 진단 → DBA 에스컬레이션 |
| A3 inventory-api | `a-hang` | 파드 멈춤 | 승인 카드에 순차 재시작·무중단 표시, `restart_pods`를 API로 실행(`ROLLING`) → 회복 |
| A3 inventory-api | `a-latency` | 외부 결제 지연 | 앱 조치 없이 고객 공지 |
| A3 inventory-api | `a-dbslow` | DB 자체 지연 | `DEPENDENCY`(postgres) 에스컬레이션, 받는 사람 A사 DBA팀, 공지, 앱 조치 없음 |
| B1 reservation-api | `b-hang` (예전 `b`) | 파드 멈춤 | 승인 카드에 전체 중단 표시, `restart_pods`를 API로 실행 → 회복 |
| B1 reservation-api | `b-pool` | 잘못된 배포 | 롤백이 계약상 불가 → `ACTION_BLOCKED`/`CONTRACT` 에스컬레이션(B사 운영 승인 담당), 공지, 재시작 제안 없음 |
| B1 reservation-api | `b-surge` | 트래픽 급증 | 확장이 계약상 불가 → `ACTION_BLOCKED`/`CONTRACT` 에스컬레이션, 공지 |
| B1 reservation-api | `b-pool-dbslow` | 잘못된 배포 + DB 지연 (겹친 장애) | 직전 변경이 있어 풀 고갈은 독립 원인. 처리할 원인 RB-STD-01 → 롤백 계약상 불가 `ACTION_BLOCKED` 에스컬레이션. 원인 후보에 RB-STD-05도 있다 |
| B2 checkin-api | `b-latency` (예전 `b2`) | 외부 결제 지연 | 재시작하지 않고 고객 공지 |
| B2 checkin-api | `b-hang-latency` | 파드 멈춤 + 결제 지연 (겹친 장애) | 처리할 원인 RB-STD-02 → 재시작(레플리카 1, 전체 중단 표시) → 미회복(가려져 있던 결제 지연) → 다시 진단해 RB-STD-04 → 고객 공지. 재시작한 새 파드도 결제 지연을 이어받는다. 인시던트 하나 | B사는 결제 API가 의존 대상이라 의존 대상 상태도 근거다

- **실행 순서:** 공통 묶음(장애가 없을 때 돌린다)을 먼저 돌리고, 서비스 묶음 5개(A1 `order-api`, A2 `cart-api`, A3 `inventory-api`, B1 `reservation-api`, B2 `checkin-api`)를 동시에 돌린다. 한 서비스 안에서는 순서대로 돈다(장애가 섞이고 같은 서비스의 알림은 인시던트 하나로 묶인다). 서비스가 다르면 인시던트·관측·회복 확인·설정 이력이 따로다([SPEC-e2e-speed](specs/SPEC-e2e-speed.md)). `--lane B1`로 한 묶음만 돌린다.
- **정리:** 시나리오마다 그 서비스의 장애를 모두 뺀다(관리 API 장애, 의존 장애 ConfigMap, 부하 발생기 0·기본 건수, 풀 20, HPA 최대 3 복원 커밋). HPA 축소는 평소 크기가 필요한 다음 시나리오(`a-surge*`, `a-dbslow-backlog`, `guard`)만 시작 전에 기다린다.
- **승인:** 스크립트가 제안을 검사해 기대한 조치만 승인하고, 나머지는 거절한다. 잘못된 조치가 실행되지 않게 하기 위해서다. 실행하는 동안 backend 자동 승인을 끄고, 끝나면 되돌린다.
- **판정:** 기대한 조치를 한 번만 제안했는지, 반영 방식, 회복 확인, 보고서·공지, 정리 상태를 본다. 승인 요청은 저장된 이벤트로 판정하므로 자동 승인으로 진행돼도 기준이 같다.
- **겹친 장애 판정** ([SPEC-compound-faults](specs/SPEC-compound-faults.md) 7장): 원인 순위는 Agent가 정하므로 E2E가 그 판단을 본다. 진단마다의 처리할 원인(작업 이벤트 STEP), 원인 후보에 기대한 조항이 있는지, 조치를 기대한 순서로 제안했는지, 같은 앱 조치를 두 번 하지 않았는지, 인시던트가 하나만 열렸는지. 작업이 끝난 뒤 울린 알림이 만든 작업은 승인 대기로 남지 않게 거절해 끝낸다.
- **장애 사이 간격:** 같은 서비스에서 앞 장애의 인시던트가 닫혀야(알림 해제 반영) 다음 장애를 주입한다. 열린 인시던트가 있으면 새 알림이 거기에 묶여 새 작업이 생기지 않는다. 따로 쉬지 않는다. Agent가 같은 서비스의 직전 인시던트가 닫힌 시각부터 로그를 보므로 앞 장애의 로그가 다음 진단 근거에 섞이지 않는다.
- **결과:** `.local/e2e/<시각>/` 폴더(git에 올리지 않음): `summary.md`(실행 중에도 갱신), 시나리오별 `<키>.log`·`<키>.md`(판정, 작업 링크, 보고서).
- **임시 브랜치:** 실행 동안 A사 설정 저장소 기준을 `e2e/<시각>` 브랜치로 바꾼다(backend `tenants.gitops_branch`, ArgoCD `targetRevision`). 장애 주입 커밋·롤백 PR·병합이 그 브랜치에서 일어나 `main`에 남지 않고, 실행 중 다른 사람이 `main`에 푸시해도 A사가 다시 동기화되지 않는다. 끝나면(실패·중단 포함) `main`으로 되돌리고 브랜치를 지운다. 남았으면 사전 점검이 잡고 `--cleanup`으로 정리한다. PR 기록은 GitHub에 닫힌 PR로 남는다([SPEC 5.8](specs/SPEC-fault-based-actions.md)).
- 실행이 겹치면 두 번째 실행은 시작하지 않는다.

### 고친 곳에 맞는 시나리오

개발 중에는 고친 곳에 맞는 시나리오만 돌리고, 전체 18개는 커밋 전에 한 번 돌린다.

| 고친 곳 | 돌릴 시나리오 |
|---|---|
| `prompts/diagnose.md`, `signals.py`, `action_guards.py` | `--quick` + `a-dbslow-backlog` `b-pool-dbslow` `b-hang-latency` |
| `prompts/propose.md` | `--quick` + `a-surge-max` `b-pool` |
| backend 조치·계약 (`ChangeService`, `RuntimeAdapter`, `GitOpsRepository`) | `guard` `a-pool` `b-pool` `b-surge` |
| backend 알림·인시던트 | `b-hang-latency` `b-hang` |
| 문서 저장소·색인 | `documents` `question` |
| 권한·토큰 | `isolation` `guard` |
| 데모 앱·`inject.py` | 고친 장애 종류의 A사·B사 시나리오 |

### 장애 주입 (`scripts/inject.sh`)

E2E 밖에서 손으로 장애를 넣고 뺄 때 쓴다. E2E도 같은 코드(`scripts/inject.py`)를 쓴다.

```bash
scripts/inject.sh tenant-b hang            # 작업 스레드 고갈
scripts/inject.sh tenant-a dbslow          # DB 자체 지연
scripts/inject.sh tenant-b surge           # 트래픽 급증 (부하 발생기 켬)
scripts/inject.sh tenant-a pool            # 잘못된 설정 배포. ArgoCD가 main을 따라가면 --allow-main이 필요하다
scripts/inject.sh tenant-a hang+latency    # 겹친 장애. 결제·DB 지연은 ConfigMap dependency-faults-<서비스>에도 적어 재시작해도 남는다
scripts/inject.sh tenant-a hang --service cart-api   # 서비스를 고른다 (없으면 order-api·reservation-api)
scripts/inject.sh tenant-a clear           # 그 고객사의 모든 서비스를 원래대로
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


## 서비스 의존성과 장애 영향 분석 (2026-10-22)

- `ImpactServiceTest`: 공유 DB의 모든 호출자 및 내부 서비스 간접 경로, 순환 종료,
  같은 이름·다른 종류 의존 대상 구분, 실제 이상과 연결 가능성 분리, 서비스별 호출 지연,
  고객사·역할 격리, 누락·무호출·비정상 숫자와 경계값 판정, 빈 카탈로그를 검사한다.
- `ImpactMetricsTest`: 로컬 HTTP 대역으로 Prometheus 다섯 쿼리의 고객사 조건·동일 조회 시각,
  다른 고객사 응답 제외, NaN·음수 제외, 조회 실패 시 빈 지표와 경고, 라벨 이스케이프를 검사한다.
- `front/tests/impact.test.js`: 실제 Vue 컴포넌트를 메모리 렌더러에 올려 지표 표시와 노드 선택,
  고객사 전환 시 늦게 도착한 이전 응답 무시, 갱신 실패 시 이전 관측값 제거를 검사한다.

검증: backend 60개(DB 포함, 실패·건너뜀 0), frontend 16개 통과, frontend 배포 빌드 통과.
실행 중인 backend에서 관리자 영향 조회 200, 고객사 담당자 및 권한 밖 고객사 조회 403을 확인했다.
브라우저에서 A사 주문 API→DB, B사 예약 API→결제 서비스 및 별도 정산 배치 표시와 고객사 전환을 확인했다.
이 로컬 환경에는 Prometheus가 실행되지 않아 실제 지표는 확인 불가로 표시한다.
정상·지연·공유 의존 대상의 다중 서비스 영향은 대역 지표와 테스트 카탈로그로 검증했으며,
실제 여러 서비스에 장애를 주입하는 E2E는 실행하지 않았다.


## Slack 확인·담당·완료 추적 (2026-10-22)

`SlackDeliveryDatabaseTest`에 V14 적용과 실제 PostgreSQL 트랜잭션 검증을 추가했다.
서명된 ACK/START/COMPLETE 요청, 재전송 중복 방지, 위조 서명·만료 시각·다른 팀·위험 응답 주소 거절,
미등록 사용자의 비공개 안내, 고객사/역할/담당자 권한, 동시 배정 단일 성공, 설정 변경 후 기존 버튼 무효화를 검사한다.
Slack 원문 갱신은 HTTP 대역으로 응답 URL 호출·대기열 완료를 검증하며 실제 외부 메시지를 보내지 않는다.
인시던트 상태를 바꾸지 않는지와 Signing Secret/응답 URL 암호화도 검사한다.

`front/tests/slack-tracking.test.js`는 실제 Vue 컴포넌트의 버튼 동작, API 인자와 처리 이력 표시,
타 담당자 완료 버튼 숨김, 배정 충돌 시 오류 표시를 확인한다.
검증 결과: backend 66개(DB 포함), frontend 19개 통과. 프론트 배포 빌드 통과.
실제 SKALA Slack 버튼 콜백은 공개 HTTPS 주소·Signing Secret·사용자 매핑을 설정한 뒤 별도로 검증해야 한다.

로컬 실행 서버에서 V14 마이그레이션 적용, 실제 HTTP ACK/START/COMPLETE의 204 응답,
이력 조회 및 브라우저 표시, Viewer의 조회/설정 403을 확인했다.
기존 테스트 발송 건의 웹 처리만 수행했으며 새 Slack 메시지는 보내지 않았다.


## 로그·에러 화면 첨부 분석 (2026-10-22)

- `TaskAttachmentsTest`: 실제 PNG/UTF-8 검사, MIME 위장·손상/빈 파일·형식·용량·개수 제한,
  고객사/역할/내부 고객사 헤더 거부, 안전한 다운로드 헤더, multipart 바인딩을 검사한다.
- `AttachmentDatabaseTest`: V15와 실제 PostgreSQL을 사용한다. 작업·파일·outbox의 동시 저장,
  저장 실패 시 전체 롤백, outbox에서 파일 본문 제외를 확인한다.
- Agent `test_attachments.py`: 줄 번호·가림 처리·긴 로그 생략, 실제 image_url 멀티모달 입력 구성,
  열린 장애가 없어도 분석하는 분기, 변경 도구 차단, 파일/지표/런북의 전체 Graph 연결,
  MCP 일부 실패 시 분석 유지, 이미지 원문을 State에 넣지 않는 동작을 대역 모델로 검사한다.
- Frontend: 실제 Vue 폼에서 첨부만으로 제출, 고객사 변경 시 파일 초기화, 오류 시 첨부 보존,
  FormData 요청과 파일 제한을 검사한다.

자동 검증: 최신 main의 관리자 장애 종료 기능을 통합한 상태에서 backend 76개(DB 포함, 실패·건너뜀 0),
frontend 25개 통과 및 배포 빌드 성공. Agent 73개도 통과했다.
Agent 테스트에는 인증 실패 시 키나 API 오류 본문을 노출하지 않고 설정 안내를 반환하는 검사도 포함한다.

실제 연동 검증: 사용자가 브라우저에서 합성 로그와 에러 화면 PNG를 업로드한 작업을 재실행해,
OpenAI `gpt-5-nano`의 이미지·로그 분석과 최종 리포트 저장(`DONE`)을 확인했다.
로컬 `.env`의 키를 적용했고, 해당 키에 `gpt-5-mini` 사용 권한이 없어 로컬 주 모델을
기존 예비 모델인 `gpt-5-nano`로 변경했다. 저장소 기본 모델 설정은 유지한다.
테스트 결과는 커넥션 풀 고갈·타임아웃을 원인 후보로 제시하고 파일·줄 번호 근거를 포함했다.
현재 로컬 MCP 서버가 실행되지 않아 실제 모니터링 지표 비교와 런북 검색은 검증하지 못했다.
자동 테스트의 모델 호출은 대역이며, 위 실제 연동 검증과 구분한다.


# 테스트

| 종류 | 무엇을 확인하나 | 실행 | 걸리는 시간 |
|---|---|---|---|
| 전체 시나리오 E2E | 장애 주입부터 승인·조치·회복 확인·보고서까지 실제 스택에서 | `python3 scripts/e2e.py` | 전체 약 20분 |
| 단위·DB 테스트 | 프론트 타임라인, Agent 도구 결과·분기, backend 소유권·승인·SLA | 아래 2장 | 1분 안팎 |

## 1. 전체 시나리오 E2E (`scripts/e2e.py`)

compose 스택과 kind 클러스터(`scripts/cluster-up.sh`)가 떠 있어야 한다. 시나리오 A는 backend에 `GITHUB_TOKEN`이 있어야 한다.
문서가 추가·수정된 커밋을 받은 뒤에는 indexer를 다시 실행한다. 사전 점검은 공통 표준 런북의 현재 파일 버전이 RAG DB에 있는지 확인한다.

```bash
python3 scripts/e2e.py                 # 전체: 사전 점검 → 격리 → 문서 질문 → B → B2 → A → A-hang
python3 scripts/e2e.py b a-hang        # 고른 시나리오만 (사전 점검은 항상)
python3 scripts/e2e.py --list          # 시나리오 목록
python3 scripts/e2e.py a --no-merge    # 롤백 PR은 사람이 병합 (병합될 때까지 기다린다)
python3 scripts/e2e.py --auto-approve  # backend 자동 승인 설정을 그대로 쓴다
```

| 키 | 시나리오 | 기대 결과 |
|---|---|---|
| `isolation` | D. 고객사 격리 | B사 담당자·B사 작업·B사 ServiceAccount로 A사 자원 접근 거부, 감사 로그 |
| `guard` | backend 방어선 | Agent를 거치지 않고 내부 API로 요청: A사 확장 6(안전 상한 4)·축소 1(하한 2), B사 Docker 서비스 재시작, B사 롤백이 모두 422로 거부되고 클러스터가 바뀌지 않는다 |
| `question` | 문서 질문 | RAG로 계약 조항을 찾아 답하고 출처를 단다 |
| `b` | B. 파드 멈춤 (B사, DIRECT) | `restart_pods`를 Kubernetes API로 실행, 회복 |
| `b2` | B2. 외부 결제 API 지연 (B사) | 재시작하지 않고 고객 공지 |
| `a` | A. 잘못된 설정 배포 (A사, GITOPS) | 롤백 PR → 병합 → ArgoCD 동기화 → 회복, 풀 크기 20 원복 |
| `a-hang` | A-hang. 파드 멈춤 (A사, GITOPS) | 승인 카드에 현재 레플리카·무중단 순차 재시작 영향을 표시하고, `restart_pods`를 Kubernetes API로 실행 → 실제 `ROLLING` 결과 → 회복 |

- **승인:** 스크립트가 제안을 검사해 기대한 조치만 승인하고, 나머지는 거절한다. 잘못된 조치가 실행되지 않게 하기 위해서다. 실행하는 동안 backend 자동 승인을 끄고, 끝나면 되돌린다.
- **판정:** 기대한 조치를 한 번만 제안했는지, 반영 방식, 회복 확인, 보고서·공지, 정리 상태를 본다. 승인 요청은 저장된 이벤트로 판정하므로 자동 승인으로 진행돼도 기준이 같다.
- **장애 사이 간격:** 같은 고객사에서 앞 장애를 정리하고 75초가 지나야 다음 장애를 주입한다. Agent는 인시던트 시작 2분 전부터 로그를 보므로, 간격이 짧으면 앞 장애의 로그가 다음 진단 근거에 섞인다.
- **결과:** 화면 요약 표와 `.local/e2e/<시각>.md`(작업 링크·보고서 포함, git에 올리지 않음).
- **주의:** 지금은 시나리오 A가 실행할 때마다 `main`에 커밋 3개(장애, 되돌리기, 병합)를 남긴다. 임시 브랜치로 바꿀 예정이다([SPEC 5.8](specs/SPEC-fault-based-actions.md)). 실행이 겹치면 두 번째 실행은 시작하지 않는다.
- 장애 형태 × 고객사 교차 매트릭스로 확장하는 계획: [specs/SPEC-fault-based-actions.md](specs/SPEC-fault-based-actions.md)

## 2. 작업 결과·승인 회귀 검증

### 변경된 동작

- MCP 도구의 오류 상태와 호출 ID를 메시지와 TOOL_END 이벤트에 보존한다. 완료된 작업 안에서도 실패한 도구는 실패로 표시한다.
- 리포트와 요약에 조치 결과, PR 생성, 동기화 상태를 전달한다. 승인은 실행 성공과 구분한다.
- incidentId가 없는 질문은 실행안을 만들지 않고 보고서로 종료한다. 인시던트가 연결된 질문 및 자동 진단은 기존 승인·실행 흐름을 사용한다.
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

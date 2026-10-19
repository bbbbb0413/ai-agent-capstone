# 작업 결과·승인 회귀 검증

## 변경된 동작

- MCP 도구의 오류 상태와 호출 ID를 메시지와 TOOL_END 이벤트에 보존한다. 완료된 작업 안에서도 실패한 도구는 실패로 표시한다.
- 리포트와 요약에 조치 결과, PR 생성, 동기화 상태를 전달한다. 승인은 실행 성공과 구분한다.
- incidentId가 없는 질문은 실행안을 만들지 않고 보고서로 종료한다. 인시던트가 연결된 질문 및 자동 진단은 기존 승인·실행 흐름을 사용한다.
- 작업 생성과 보고서 저장에서 인시던트 소유 고객사를 검증한다.
- 승인은 DB에서 대상 pending_action과 상태를 비교해 한 요청만 처리한다. 기록을 커밋한 뒤 Agent를 재개하고, 전달 실패 시 같은 승인 카드를 복원한다.
- approvalId로 승인 대상을 식별한다. 단일 Agent 프로세스 내 동시 재개를 직렬화하고, 지난 승인 ID로 새로운 승인 단계를 재개하지 않는다. Agent 다중 인스턴스 배포에는 별도 분산 잠금/실행 멱등성 보장이 필요하다.
- SLA 기한은 A사 P1/P2=15/60분, B사 P1/P2=60/240분으로 계약서와 일치시킨다.
- GITOPS 고객사라도 파드 재시작은 Kubernetes API로 바로 반영된다고 표시한다.

Harness 시간·턴 한도, 원인 미확정 시 RAG 분기, 시나리오 초기화 동작은 변경하지 않는다.

## 자동 테스트

프론트 타임라인·조치 결과·승인 영향 안내:

```bash
cd front
npm test
```

Agent의 MCP 어댑터 오류 보존, 조치 실패 분기, 리포트 입력, 동시 재개:

```bash
docker compose build agent
docker compose run --rm --no-deps -T \
  -v ./server/python/agent/tests:/tests:ro \
  --entrypoint /app/.venv/bin/python \
  agent -m unittest discover -s /tests -v
```

Backend의 소유권, 관리자 승인, 전달 실패 복원, 자동 승인, SLA:

```bash
docker build --target build -t opslens-backend-tests server/spring/backend
docker run --rm opslens-backend-tests ./gradlew --no-daemon test
```

DB 통합 테스트는 OPSLENS_TEST_DATABASE_URL을 지정했을 때만 실행한다. 반드시 별도의 폐기 가능한 PostgreSQL을 사용한다. 테스트는 생성한 review_UUID 스키마를 정리한다. 동시 승인 두 건 중 한 건만 성공하는지, 재시도 복원이 새 승인 정보를 덮어쓰지 않는지, 다른 고객사 보고서 수정이 차단되는지 확인한다.

## 로컬 실행 확인

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build backend agent frontend
```

Admin/Engineer/Viewer 로그인과 조회, 로그아웃 후 401, 일반 질문 완료, 승인·거절, 도구 오류 표시를 확인한다. 실제 재시작·롤백·외부 공지 없이도 실패 응답과 승인 거절 경로는 검증할 수 있다.

실제 고객사 장애 주입·조치·복구 검증에는 별도 kind 환경과 고객사 접속 정보가 필요하다. 단위/DB 테스트 통과만으로 해당 시나리오 전체 성공을 의미하지 않는다.

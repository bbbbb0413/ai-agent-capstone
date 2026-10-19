# Spec: 사용자별 채팅 기록과 재연결

> 상태: 초안 (승인 대기) · 작성일: 2026-10-19

## 1. 목적

### 지금의 문제

| 상황 | 현재 동작 | 원인 (코드) |
|---|---|---|
| 답변이 생성되는 중에 새로고침 | 지금까지 흘러온 답변(TOKEN)과 진행 상황(TOOL_START/END)이 사라지고, 끝날 때까지 빈 화면 | 진행 이벤트를 저장하지 않고 메모리에서만 중계 (`TaskEventHub`) |
| 지난 질문을 다시 보기 | 질문 목록 화면이 없음. 작업 URL을 기억해야만 볼 수 있음 | 화면에 내 작업 목록이 없음 (`IncidentsView`는 인시던트 작업만 링크) |
| 이어서 질문하기 | 질문마다 독립된 작업이라 앞 대화를 모름 | 작업에 대화 묶음 개념이 없음 |
| backend 재시작·네트워크 끊김 | 화면이 다시 붙어도 놓친 이벤트를 받을 수 없음 | SSE에 이벤트 번호(`id`)가 없고, 재연결 시 재전송 경로가 없음 |

### 만들 것

MSP 엔지니어·관리자가 **대화(conversation) 단위로 Agent와 질문을 주고받고**, 그 기록이 계정에 남으며, **새로고침·재연결해도 놓친 내용 없이 이어서 보는** 채팅 화면.

### 사용자 이야기

- 엔지니어로서, 왼쪽 목록에서 내가 했던 대화를 골라 다시 볼 수 있다.
- 엔지니어로서, 답변이 생성되는 중에 새로고침해도 이미 나온 답변과 진행 상황이 그대로 보이고, 이어서 실시간으로 받는다.
- 엔지니어로서, 승인 대기 중인 질문은 새로고침 후에도 승인 카드가 보인다.
- 엔지니어로서, 같은 대화에서 "그럼 B사는?"처럼 앞 질문을 이어받아 물을 수 있다.
- 다른 사용자는 내 대화를 볼 수 없다.

## 2. 가정 (틀리면 지금 바로 알려주세요)

1. **대화는 여러 질문을 묶고, Agent는 같은 대화의 앞 질문·답변을 참고한다.** 질문 하나는 지금처럼 작업(ai_task) 하나다.
2. **대화 하나는 고객사 하나에 묶인다.** 고객사 격리 원칙 때문에 대화 중간에 고객사를 바꾸지 않는다. 다른 고객사는 새 대화로 묻는다.
3. **대화는 만든 사람만 본다.** MSP_ADMIN도 남의 대화는 보지 않는다(감사 로그로 대신 추적).
4. **알림으로 시작된 진단 작업(DIAGNOSE)은 채팅 목록에 넣지 않는다.** 지금처럼 인시던트 화면에서 본다. 다만 재연결·이벤트 재전송은 모든 작업에 똑같이 적용된다.
5. **TENANT_VIEWER는 대화를 만들 수 없다** (현재 권한 그대로).
6. 진행 이벤트(TOKEN 포함)는 **모두 DB에 저장**한다. 데모 규모(작업당 수백 건)라 용량 문제는 없다고 본다.
7. 저장소는 지금의 PostgreSQL `app` 스키마. 새 서비스(Redis 등)는 추가하지 않는다.

## 3. 범위

### 하는 것

- 대화 테이블과 작업-대화 연결, 진행 이벤트 저장 (Flyway V3 마이그레이션)
- 대화 API: 목록, 생성, 상세(질문·답변 목록), 질문 추가
- SSE 재연결: 이벤트마다 순번(`id`)을 붙이고, 재연결 시 `Last-Event-ID` 이후 이벤트를 DB에서 다시 보낸 뒤 실시간으로 이어 보냄
- 이어 질문: backend가 같은 대화의 최근 질문·답변을 작업 메시지에 담아 agent에 넘기고, agent가 진단 프롬프트에 넣음
- 화면: `/chat`(대화 목록 + 대화창), `/chat/:conversationId`. 새로고침하면 같은 대화와 진행 상태를 복원

### 하지 않는 것

- 대화 공유, 대화 검색, 대화 이름 바꾸기·삭제 (필요하면 다음 단계)
- 여러 backend 인스턴스 간 실시간 중계 (단일 인스턴스 전제, 재전송은 DB로 해결)
- 이벤트 보관 기간 정책과 자동 삭제
- 알림 기반 DIAGNOSE 작업을 채팅 목록에 노출

## 4. 설계

### 4.1 데이터 (Flyway `V3__conversations.sql`)

```sql
CREATE TABLE conversations (
    id          UUID PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id),
    tenant_id   TEXT        NOT NULL REFERENCES tenants (id),
    title       TEXT        NOT NULL,              -- 첫 질문 앞 40자
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversations_user ON conversations (user_id, updated_at DESC);

ALTER TABLE ai_tasks ADD COLUMN conversation_id UUID REFERENCES conversations (id);

CREATE TABLE task_events (
    task_id     UUID        NOT NULL REFERENCES ai_tasks (id),
    seq         INT         NOT NULL,              -- 작업 안에서 1부터 증가
    type        TEXT        NOT NULL,              -- TOKEN | TOOL_START | TOOL_END | APPROVAL_REQUIRED | DONE | FAILED
    data        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, seq)
);
```

기존 V1·V2 마이그레이션은 고치지 않는다.

### 4.2 API (화면 → backend, 모두 JWT 필요)

| Method | Path | 설명 | 응답 |
|---|---|---|---|
| GET | `/api/conversations` | 내 대화 목록 (최근 순, 최대 50) | `[{id, tenantId, title, updatedAt, lastStatus}]` |
| POST | `/api/conversations` | 대화 만들기 + 첫 질문 `{tenantId, question}` | 202 `{conversationId, taskId}` |
| GET | `/api/conversations/{id}` | 대화 상세: 질문·답변·상태·승인 대기 정보 | `{id, tenantId, title, messages:[{taskId, question, status, answer, sources, pendingAction, createdAt}]}` |
| POST | `/api/conversations/{id}/messages` | 이어 질문 `{question}` | 202 `{taskId}` |
| GET | `/api/ai/tasks/{id}/stream` | (변경) 이벤트에 `id: <seq>`를 붙인다. `Last-Event-ID` 헤더나 `?after=<seq>`가 있으면 그 뒤 이벤트를 먼저 다시 보낸다 | text/event-stream |

- 남의 대화나 작업에 접근하면 **404**를 돌려준다(존재 여부를 숨긴다).
- 이전 질문이 아직 끝나지 않았으면(QUEUED·RUNNING·WAITING_APPROVAL) 이어 질문은 **409**로 막는다. 같은 대화에서 Agent 두 개가 동시에 돌지 않게 한다.
- 기존 `POST /api/ai/tasks`는 호환을 위해 남기되, 화면은 새 API만 쓴다.

### 4.3 이벤트 저장과 재연결

```
agent ──POST /internal/tasks/{id}/events──▶ backend
                                            ├─ 1) task_events에 seq를 매겨 저장 (작업 단위로 순서 보장)
                                            └─ 2) 연결된 SSE에 id: seq 로 전송
브라우저 재연결 (EventSource 자동 재시도, Last-Event-ID 자동 첨부)
  └─▶ backend: 구독 등록 → DB에서 seq > Last-Event-ID 이벤트 재전송 → 이후 실시간
      (재전송과 실시간 사이에 같은 seq가 겹치면 화면이 seq로 중복을 버린다)
```

- 작업이 이미 DONE·FAILED면 남은 이벤트를 보내고 스트림을 닫는다.
- SSE 연결 유지를 위해 15초마다 주석 줄(heartbeat)을 보낸다(nginx `proxy_read_timeout` 1시간 안).

### 4.4 이어 질문 (agent 쪽)

- backend가 작업 메시지(`TaskMessage`)에 `history`를 넣는다: 같은 대화의 **끝난 앞 질문 최대 5개**의 `{question, answer}`(답변은 앞 1,500자).
- agent는 diagnose 프롬프트의 사용자 메시지 앞에 "이전 대화" 절로 넣는다. Harness의 Context 예산(24,000자)을 그대로 따른다.
- agent는 DB를 읽지 않는다(레퍼런스 금지 사항). 기록은 backend가 넘긴다.

### 4.5 화면

- `/chat`: 왼쪽 대화 목록(고객사·제목·상태), 오른쪽 대화창. 상단 "새 대화"에서 고객사를 고르고 첫 질문.
- 대화창: 질문 말풍선 → 답변(마크다운 원문), 진행 상황 접힘 목록, 승인 카드, 근거 목록.
- 진행 중인 질문이 있으면 열 때 `GET /api/conversations/{id}`로 상태를 받고, 그 작업의 스트림을 `?after=0`으로 구독해 처음부터 복원한다.
- 상단 메뉴에 "채팅" 링크. 기존 인시던트 화면의 질문 입력칸은 `/chat`으로 옮긴다.

## 5. 기술 스택

기존 그대로: Spring Boot 3.5.16 (JdbcClient, Flyway, SseEmitter), Vue 3.5 + vue-router 4 + Pinia 3, FastAPI + LangGraph 1.2. **새 의존성 없음** (테스트용 의존성은 Open Question 6).

## 6. 명령

```
backend 빌드:   cd server/spring/backend && ./gradlew build
backend 테스트: cd server/spring/backend && ./gradlew test
agent 테스트:   cd server/python/agent && uv run --frozen python -m pytest   (pytest 추가 시)
front 빌드:     cd front && npm run build
전체 기동:      docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile queue up -d --build
```

## 7. 프로젝트 구조 (바뀌는 곳)

```
server/spring/backend/src/main/java/com/opslens/backend/
  conversation/        ConversationController, ConversationRepository (새 패키지)
  task/                TaskEventHub(재전송), TaskRepository(이벤트 저장), TaskService(history), TaskMessage(history 필드)
server/spring/backend/src/main/resources/db/migration/V3__conversations.sql
server/spring/backend/src/test/java/...            (새로 생김)
server/python/agent/src/agent/{graph.py, runner.py, main.py}   history 받기·프롬프트 반영
front/src/views/ChatView.vue, front/src/api.js, front/src/router.js, front/src/App.vue
docs/OpsLens-기획안.md   API 계약(5장) 갱신
```

## 8. 코드 스타일

기존 코드를 따른다: 생성자 주입, record DTO, `JdbcClient` 직접 SQL, 한국어 주석은 "왜"만, 권한 판단은 backend(`CurrentUser`).

```java
@GetMapping("/{id}")
ConversationView get(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
    // 남의 대화는 존재 여부도 숨긴다
    return conversations.findOwned(id, CurrentUser.from(jwt).id())
            .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
}
```

## 9. 테스트 전략

| 수준 | 대상 | 방법 |
|---|---|---|
| 단위 (backend) | 이벤트 seq 증가, 재전송 범위(`after`), 남의 대화 404, 진행 중 이어 질문 409, history 구성(최대 5개·1,500자) | JUnit 5 + Spring Boot Test (테스트 DB는 Open Question 6) |
| 단위 (agent) | history가 diagnose 프롬프트에 들어가는지 | 지난번처럼 가짜 모델로 Graph 실행 |
| 통합 (스택) | 아래 성공 기준 1~6 | `scripts/check-chat.sh`(curl로 로그인 → 질문 → 스트림 끊고 다시 붙기 → backend 재시작) |
| 화면 | 새로고침 복원, 승인 카드 복원 | 브라우저 수동 확인 |

## 10. 경계

- **항상:** 새 마이그레이션 파일로만 스키마 변경, 권한은 backend에서 판단, 빌드·테스트 통과 후 커밋
- **먼저 묻기:** 새 의존성 추가(테스트 DB 포함), 기존 API 응답 형식 변경, 이벤트 보관 기간 정책
- **하지 않기:** V1·V2 마이그레이션 수정, agent가 DB를 직접 읽기, 남의 대화 노출, `.env` 읽기

## 11. 성공 기준

1. 답변 생성 중 새로고침하면, 새로고침 전까지 받은 답변과 진행 상황이 **빠짐없이, 중복 없이** 다시 보이고 이어서 실시간으로 받는다.
2. 승인 대기 중인 질문을 새로고침하면 승인 카드가 보이고, 승인하면 이어서 진행된다.
3. 로그아웃 후 다시 로그인해도 내 대화 목록과 각 대화의 질문·답변이 그대로 있다.
4. 다른 사용자(engineer ↔ admin, viewer)가 내 대화나 그 작업에 접근하면 404다.
5. backend 컨테이너를 재시작해도, 열려 있던 화면이 자동으로 다시 붙어 놓친 이벤트를 받는다.
6. 같은 대화의 두 번째 질문을 처리할 때 agent가 받은 작업 메시지에 첫 질문·답변이 들어 있다. 진행 중에 보낸 이어 질문은 409다.

## 12. 열린 질문

1. **이어 질문의 맥락 유지가 필요한가요?** (가정 1) 아니면 "질문 기록 목록 + 재연결"만으로 충분한가요? 맥락 유지는 agent 변경이 들어가서 작업량이 늘어납니다.
2. **대화를 만든 사람만 보게 할까요?** (가정 3) 같은 고객사 담당 엔지니어끼리 공유하거나, MSP_ADMIN이 전체를 보게 할 수도 있습니다.
3. **알림 기반 진단 작업도 채팅 목록에 보일까요?** (가정 4)
4. **TOKEN 이벤트를 하나하나 저장할까요?** 답변 하나에 수백 건이 쌓입니다. 대안은 완료 시 하나로 합쳐 저장하는 것인데, 그러면 생성 중 새로고침 때 TOKEN을 다시 보내기 위해 별도 처리가 필요합니다.
5. 대화 목록 50개, 이전 질문 5개, 답변 1,500자 같은 숫자가 괜찮은가요?
6. **backend 테스트 DB:** 지금은 테스트가 없습니다. Testcontainers(PostgreSQL)를 테스트 의존성으로 추가해도 될까요? 안 되면 스택 통합 스크립트로만 검증합니다.

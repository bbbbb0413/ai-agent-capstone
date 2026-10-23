# Spec: 근거 문서 저장소 (S3 저장소 + 업로드 + 형식별 비동기 색인)

> 상태: 완료 · 작성일: 2026-10-23 · 검증: 단위 테스트(backend, indexer 35, rag-mcp 8, agent 104, front 36), 전체 E2E 15개 통과(2026-10-23, `documents` 11초)

## 1. 목적

### 지금의 문제

| 상황 | 지금 | 원인 |
|---|---|---|
| 런북·SLA 계약서를 고친다 | 저장소의 `data/documents/{고객사}/*.md`를 고치고 커밋한 뒤 `indexer`를 손으로 돌린다 | 문서가 코드 저장소의 파일이고, 색인은 `tools` 프로필의 일회성 컨테이너다 |
| 계약서가 PDF·Word로 온다 | 받을 수 없다. 사람이 Markdown으로 옮겨 적어야 한다 | 색인기가 Markdown의 `## ` 제목만 안다 |
| 누가 언제 바꿨는지 본다 | git 이력뿐이다. 화면에서는 볼 수 없다 | 문서 메타데이터가 없다 |
| 이전 내용으로 되돌린다 | git으로 되돌리고 다시 색인한다 | 버전 개념이 없다 |
| 권한 | 저장소에 쓸 수 있으면 어느 고객사 문서든 바꾼다 | 문서에 고객사·역할 검사가 없다 |
| 근거 원문 보기 (`read_document`) | 조각(`docs.chunks`)을 이어 붙여 보여 준다. 첫 `## ` 앞의 머리말은 빠진다 | 원본을 따로 보관하지 않는다 |

### 만들 것

- 원본 문서는 **S3 호환 저장소(SeaweedFS)**에 버전별로 보관한다. 이전 버전도 지우지 않는다.
- **Markdown·텍스트·PDF·Word(docx)**를 받는다. 공통 표준 런북도 같다.
- **형식별로 색인한다.** 형식마다 전용 파서가 본문과 제목 구조(Word 제목 스타일, PDF 글자 크기, Markdown `#`)와 표를 뽑고, 공통 청커가 제목 경로 단위로 조각을 만든다. 구현은 `ai-web-service`의 index-worker(`server/python/index-worker/app/parse.py`, `chunk.py`, 규칙 문서 `contracts/indexing/README.md`)를 옮겨 와 OpsLens에 맞게 줄인다.
- MSP 관리자와 엔지니어가 **화면에서 업로드**한다. 공통 문서(`_common`)는 관리자만, 고객사 문서는 관리자와 **그 고객사를 담당하는 엔지니어**만 올린다.
- 업로드하면 backend가 원본을 저장하고 **색인 대기**로 기록한 뒤, 작업 전달과 같은 **Transactional Outbox → Kafka**로 색인을 요청한다. **파싱·청킹·임베딩은 indexer가 비동기로** 한다. 화면에 색인 상태(대기·완료·실패 사유)가 보인다.
- agent의 근거 검색은 지금처럼 pgvector(`docs.chunks`)에서 하고, **원문 참조는 S3 저장소**에서 가져온다.

### 사용자 이야기

- 엔지니어로서, 담당 고객사의 SLA 계약서 PDF를 올리면 몇 초 안에 agent의 근거 검색에 "SLA 계약서 > 제5조 서비스 크레딧"처럼 조항 경로와 함께 나온다.
- 엔지니어로서, 담당하지 않는 고객사 문서와 공통 표준 런북은 볼 수만 있고 올리거나 지울 수 없다.
- 관리자로서, 공통 표준 런북을 Word로 고쳐 올리고, 잘못 올렸으면 이전 버전으로 되돌린다.
- 관리자로서, 공통 표준 런북의 조항 번호가 빠진 문서를 올리면 색인이 거부되고, agent는 이전 버전으로 계속 동작한다.
- 관리자·엔지니어로서, 문서마다 누가 언제 어떤 버전을 올렸고 색인이 됐는지 본다.

## 2. 결정 사항

| 항목 | 결정 | 이유 |
|---|---|---|
| 지원 형식 | `md`, `txt`, `pdf`(텍스트 PDF), `docx`. 공통·고객사 문서 모두 같다 | 계약서·런북이 실제로 오는 형식이다. 스캔 PDF(OCR), `pptx`·`xlsx`는 범위 밖(11장) |
| 색인 방식 | **형식별 파서 + 공통 청커.** 파서는 확장자로 고르고, 모두 "본문 + 제목 목록(수준, 제목, 위치)"을 돌려준다. 청커는 제목으로 절을 나누고 절 경로(`A > B`)를 만든다. 긴 절만 토큰 길이로 나눈다 | 형식이 가진 구조(제목 스타일, 표)를 Markdown 변환으로 잃지 않는다. 파서가 늘어도 청커·임베딩·저장은 한 곳이다 |
| 구현 출처 | `ai-web-service` index-worker의 `parse.py`·`chunk.py`를 옮긴다. 회의록 태그(DECISION 등)와 작성일·프로젝트 헤더는 뺀다 | 형식별 파서, 표 처리, 상한, 실패 사유, 골든 테스트까지 검증된 코드다. 같은 Python이다 |
| 중복·늦은 메시지 | indexer는 받은 버전이 아직 `PENDING`(또는 다시 시도할 `FAILED`)이고 그 문서의 최신 버전일 때만 처리한다. 아니면 커밋하고 건너뛴다. 처리 결과 보고도 backend가 같은 조건으로 한 번만 받는다 | 작업 전달의 "끝난 작업은 실행하지 않음"과 같은 규칙. at-least-once 전달에서 같은 버전을 두 번 임베딩하지 않는다 |
| Kafka 없이 (`DISPATCH_MODE=http`) | relay가 보내지 않고, 점검 주기를 5초로 줄여 indexer가 내부 API(`GET /internal/documents/pending`)로 가져간다 | 작업 전달처럼 Kafka를 끄고 원인을 좁힐 때도 문서 색인이 멈추지 않는다 |
| DLT | 두지 않는다 | 실패는 DB의 상태·단계·사유로 남고, 다시 시도도 DB 기준으로 한다 |
| 임베딩 시점 | 업로드는 저장과 "색인 대기" 기록만 한다. indexer가 대기 문서를 가져가 파싱·청킹·임베딩·저장을 한다 | 업로드가 OpenAI 장애·지연과 파일 파싱에 묶이지 않는다. backend에 OpenAI 키·파서가 필요 없다 |
| 색인 요청 전달 | **Kafka.** 문서 버전 행과 outbox 행(`document_outbox`)을 한 트랜잭션에 쓰고, relay가 토픽 `documents.index`(키 = 문서 ID)로 보낸다. `document-indexer`는 consumer group으로 받는다. 메시지는 버전 ID와 종류(`INDEX`·`DELETE`)만 싣고, **상태의 기준은 DB**다. 5분마다 도는 점검이 오래 `PENDING`인 버전과 다시 시도할 실패를 다시 보낸다 | 작업 전달(`task_outbox`, `TaskOutboxRelay`, `DISPATCH_MODE=kafka` 기본)과 같은 패턴이라 새 개념이 없다. 바로 전달되고, indexer를 늘리면 consumer group이 나눈다. 같은 트랜잭션에 쓰므로 저장은 됐는데 요청이 빠지는 일이 없다. 문서 ID 키로 같은 문서의 버전 순서를 지킨다 |
| 검색에 쓰는 버전 | 문서마다 **마지막으로 색인에 성공한 버전**이다. 새 버전이 실패하면 이전 버전 조각이 그대로 남는다 | 잘못된 파일 하나로 근거가 사라지지 않는다. 화면에 "v3 색인 실패, 검색은 v2"로 보인다 |
| 공통 표준 런북 검사 | indexer가 파싱한 뒤 검사한다. 절 경로에서 찾은 조항 번호 집합(`RB-STD-01`~`07`)이 지금 검색 중인 버전과 같아야 한다. 다르면 `FAILED`(다시 시도 안 함)와 빠진·늘어난 번호를 사유로 남긴다 | agent의 조치 검사(`action_guards.CLAUSE_ACTIONS`)와 판정표가 조항 번호에 묶여 있다. PDF·Word는 backend가 업로드 때 읽을 수 없어 파싱한 쪽에서 본다 |
| 엔지니어 범위 | 담당 고객사(`user_tenants`)의 문서만 올리기·새 버전·되돌리기·삭제. 공통 문서는 볼 수만 있다 | 지금 엔지니어의 고객사 권한(`CurrentUser.canAccess`)을 그대로 쓴다 |
| 이전 버전 | 모두 보관한다. 새 버전은 새 객체로 저장하고, 되돌리기는 고른 버전을 복사한 새 버전을 만든다 | 이력이 앞으로만 늘어 누가 무엇을 되돌렸는지 남는다. DB가 버전을 관리해 목록·권한 검사가 쉽다 |
| 문서 종류 | 업로드 화면에서 고른다: 고객사 문서는 `runbook`·`sla`, 공통 문서는 `runbook`만. 파일 이름 접두어 규칙(`sla-`, `runbook-`)은 없앤다 | 파일 이름 실수로 종류가 틀어지지 않는다. `postmortem`은 종료된 인시던트에서 자동으로 만들어져 올리지 않는다 |
| 저장소 접근 | backend만 S3 계정을 갖는다. indexer와 rag-mcp는 backend 내부 API로 원본·추출 본문을 받는다 | 비밀 값이 하나 늘 뿐이고, 고객사 격리 검사(작업 토큰)가 backend 한 곳에 남는다. 외부에서 받은 파일을 해석하는 indexer에는 저장소 계정이 없다 |
| 삭제 | 문서를 "삭제됨"으로 표시하고 색인 조각을 지운다. 저장소의 버전 파일은 남긴다 | 이전 버전 보관 결정과 같다. 완전 삭제는 범위 밖이다 |

## 3. 데이터

### 3.1 S3 저장소 (SeaweedFS)

- 처음 계획은 MinIO였다. MinIO가 2025년 10월 커뮤니티 이미지 배포를 멈추고 Docker Hub 저장소를 지워(`pull access denied`) **SeaweedFS**(Apache 2.0, `chrislusf/seaweedfs:4.48`)로 바꿨다. backend는 S3 API(AWS SDK v2)만 쓰므로 저장소를 바꿔도 코드는 그대로다.
- compose에 `seaweedfs`(master·volume·filer·S3를 한 프로세스로, 데이터 볼륨 `seaweedfs-data`)와 일회성 `seaweedfs-init`을 더한다.
  - `seaweedfs`는 기동할 때 환경변수로 S3 계정 설정을 만든다. 계정은 backend 하나이고 버킷 `opslens-documents`만 읽기·쓰기·목록한다. 계정이 정의돼 있으면 익명 요청은 거부된다.
  - `seaweedfs-init`은 `weed shell`로 버킷을 만든다(있으면 그대로). master·filer에 바로 붙으므로 S3 관리자 계정이 필요 없다.
- 객체 키:
  - 원본: `{tenant_id}/{doc_id}/v{version}/original.{ext}`
  - 추출 본문: `{tenant_id}/{doc_id}/v{version}/extracted.md` (파서가 뽑은 본문. 원문 보기에 쓴다. Markdown 원본은 만들지 않는다)
  - `tenant_id`는 고객사 ID 또는 `_common`.
- 새 환경변수 (`.env.example` 맨 아래, 샘플 값): `DOCUMENTS_S3_ACCESS_KEY`, `DOCUMENTS_S3_SECRET_KEY`. 모두 필수(`:?`).
- 저장소 포트는 열지 않는다. 화면 업로드는 backend를 거친다.

### 3.2 DB (`V18__documents.sql`, app 스키마)

```sql
CREATE TABLE documents (
    id             UUID PRIMARY KEY,
    tenant_id      TEXT NOT NULL,          -- 고객사 ID 또는 '_common'
    doc_id         TEXT NOT NULL,          -- 검색 결과의 문서 id, uri의 마지막 부분 (영문 소문자·숫자·-)
    doc_type       TEXT NOT NULL,          -- runbook | sla
    title          TEXT NOT NULL,          -- 업로드 때 적은 제목
    active_version INT,                    -- 검색에 쓰는 버전 (마지막으로 색인에 성공한 버전)
    deleted_at     TIMESTAMPTZ,
    created_by     TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id)
);

CREATE TABLE document_versions (
    id                UUID PRIMARY KEY,
    document_id       UUID NOT NULL REFERENCES documents (id),
    version           INT  NOT NULL,
    original_filename TEXT NOT NULL,
    format            TEXT NOT NULL,       -- md | txt | pdf | docx (확장자로 정한다)
    object_key        TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    size_bytes        INT  NOT NULL,
    restored_from     INT,                 -- 되돌리기로 만든 버전이면 원래 버전 번호
    uploaded_by       TEXT NOT NULL,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    index_status      TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | INDEXED | FAILED | SUPERSEDED
    index_stage       TEXT,                -- 실패한 단계: PARSE | CHUNK | VALIDATE | EMBED | STORE
    index_error       TEXT,
    retryable         BOOLEAN,
    attempts          INT  NOT NULL DEFAULT 0,
    chunk_count       INT,
    indexed_at        TIMESTAMPTZ,
    UNIQUE (document_id, version)
);
```

- 문서의 최신 버전은 가장 큰 `version`, 검색 버전은 `active_version`이다. 새 버전이 `INDEXED`가 되면 `active_version`이 바뀌고, 이전에 대기·실패였던 더 낮은 버전은 `SUPERSEDED`가 된다(색인 대상이 아니다).
- 다시 시도: `retryable`인 실패만, 5분 간격, 3번까지.

```sql
-- 색인 요청 전달 대기열 (Transactional Outbox). task_outbox와 같은 모양이다
CREATE TABLE document_outbox (
    id                  BIGSERIAL   PRIMARY KEY,
    document_version_id UUID        REFERENCES document_versions (id),
    document_id         UUID        NOT NULL REFERENCES documents (id),   -- Kafka 키
    kind                TEXT        NOT NULL,                     -- INDEX | DELETE
    status              TEXT        NOT NULL DEFAULT 'PENDING',   -- PENDING | SENT | FAILED
    attempts            INT         NOT NULL DEFAULT 0,
    last_error          TEXT,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ
);
CREATE INDEX document_outbox_pending ON document_outbox (next_attempt_at, id) WHERE status = 'PENDING';
```

- 업로드·새 버전·되돌리기는 `INDEX`, 삭제는 `DELETE` 행을 같은 트랜잭션에 쓴다. 커밋 뒤 바로 보내고(`kick`), 실패하면 2^시도 초 뒤에 다시 보낸다(`TaskOutboxRelay`와 같다).
- 발행이 끝내 실패해도 버전 상태는 `PENDING`으로 남고, 5분 점검(2장)이 새 outbox 행을 만든다. 작업과 달리 문서는 실패로 끝내지 않는다.
- 메시지: `{"kind": "INDEX", "documentVersionId": "…", "documentId": "…"}`. 원본·비밀 값은 싣지 않는다.

### 3.3 색인 조각 (`docs.chunks`, indexer 스키마)

지금 칸에 더한다.

| 칸 | 값 |
|---|---|
| `section_path` | 절 경로. 예: `B사 SLA 계약서 > 제5조 서비스 크레딧`. 제목이 없는 문서는 빈 값 |
| `source_format` | `md`·`txt`·`pdf`·`docx`·`postmortem` |
| `document_version` | 문서 버전 번호 (지금의 `version`은 원본 해시라 그대로 둔다) |

- 지금 `section` 칸은 절 경로의 마지막 제목으로 채운다. 기존 소비자(`search_documents` 결과, agent 근거 인용)가 그대로 동작한다.
- 같은 문서의 새 버전이 성공하면 이전 조각을 지우고 새로 넣는다(지금 `index_document`). 실패하면 건드리지 않는다.

## 4. API

### 4.1 화면 API (`/api/documents`, 로그인 쿠키 + CSRF)

| 메서드·경로 | 동작 | 권한 |
|---|---|---|
| `GET /api/documents?tenant=` | 문서 목록: 제목, 종류, 최신·검색 버전, 색인 상태, 올린 사람·시각 | 관리자: 전부. 엔지니어: 공통 + 담당 고객사 |
| `POST /api/documents` (multipart: `file`, `tenant`, `docType`, `docId`, `title`) | 새 문서(버전 1) | 공통: 관리자. 고객사: 관리자, 담당 엔지니어 |
| `POST /api/documents/{id}/versions` (multipart: `file`) | 새 버전. 형식은 이전 버전과 달라도 된다 | 위와 같다 |
| `GET /api/documents/{id}` | 문서와 버전 이력(상태, 실패 단계·사유, 조각 수) | 목록과 같다 |
| `GET /api/documents/{id}/versions/{v}/original` | 원본 파일 받기 | 목록과 같다 |
| `GET /api/documents/{id}/versions/{v}/extracted` | 추출 본문(Markdown 원본이면 원본) 보기 | 목록과 같다 |
| `POST /api/documents/{id}/versions/{v}/restore` | 그 버전을 복사한 새 버전 | 올리기와 같다 |
| `DELETE /api/documents/{id}` | 삭제됨으로 표시, 색인 조각 삭제 요청 | 올리기와 같다 |

**업로드 때 backend가 하는 검사 (형식과 무관, 422):**
- 확장자가 `md`·`txt`·`pdf`·`docx` 중 하나이고, 파일 앞부분이 그 형식과 맞다(PDF는 `%PDF-`, docx는 ZIP 서명). 확장자만 바꾼 파일을 막는다
- 크기: `md`·`txt` 1MB, `pdf`·`docx` 20MB 이하
- 종류가 그 범위에서 허용된 값이다 (2장)
- `docId`는 영문 소문자·숫자·`-`이고, 같은 범위에 이미 있으면 409(새 버전으로 올린다)

파일 내용(구조, 조항 번호)은 backend가 해석하지 않는다. indexer가 파싱한 뒤 검사하고 실패 사유를 남긴다(5장).

**감사 로그:** `DOCUMENT_UPLOADED`(버전·형식 포함), `DOCUMENT_RESTORED`, `DOCUMENT_DELETED`. TENANT_VIEWER는 이 API를 쓰지 않는다(403).

### 4.2 내부 API

| 호출자 | 경로 | 동작 | 토큰 |
|---|---|---|---|
| indexer | `GET /internal/documents/versions/{id}` | 메시지로 받은 버전의 처리 정보: 문서, 형식, 상태, 최신 버전인지, 공통 표준 런북이면 지금 검색 버전의 조항 번호 집합 | `INDEXER_BACKEND_TOKEN` |
| indexer | `GET /internal/documents/pending` | Kafka 없이(`DISPATCH_MODE=http`) 돌 때만 쓴다. 색인할 버전과 지울 문서 목록 | 같음 |
| indexer | `GET /internal/documents/versions/{id}/original` | 원본 파일 | 같음 |
| indexer | `POST /internal/documents/versions/{id}/result` | `{status, stage, error, retryable, chunks, clauses, extracted}`. 성공이면 backend가 추출 본문을 저장소에 저장하고 `active_version`을 바꾼다 | 같음 |
| rag-mcp | `GET /internal/tenants/{tenant}/documents/{docId}` | 검색 버전의 추출 본문. 경로의 고객사가 작업 토큰의 고객사나 `_common`이 아니면 403 | `MCP_BACKEND_TOKEN` + 작업 토큰 |

`ServiceTokenFilter.ROUTES`에 `/internal/documents/**` → indexer를 더한다. rag-mcp 경로는 기존 `/internal/tenants/**` → mcp에 들어간다. `_common` 경로는 작업 토큰 검사에서 어느 고객사든 읽을 수 있게 한다(지금 rag-mcp `read_document`와 같은 규칙).

## 5. 색인 (indexer)

### 5.1 서비스

- `indexer.main`을 상시 실행하는 Kafka consumer로 바꾼다(`aiokafka`, agent의 `kafka_worker`와 같은 라이브러리). 서비스 이름은 `document-indexer`, consumer group은 `document-indexer`. `tools` 프로필의 일회성 `indexer` 서비스와 `data/documents` 볼륨 마운트는 없앤다.
- 메시지 하나를 끝까지 처리하고(결과 보고까지) 오프셋을 커밋한다. 처리 중 죽으면 같은 메시지를 다시 받고, 2장의 중복 규칙으로 걸러진다.
- 파티션 수는 작업 토픽과 같은 `KAFKA_PARTITIONS`를 쓴다. 토픽은 backend가 기동할 때 만든다(작업 토픽과 같은 방식).
- `DISPATCH_MODE=http`면 consumer 대신 5초마다 `GET /internal/documents/pending`을 본다.
- 회고 색인(`indexer.postmortems --watch`)은 그대로 둔다. 회고 조각은 같은 공통 청커를 쓰도록 바꾸지 않는다(지금 결과를 바꾸지 않는다).

### 5.2 파이프라인

```
메시지 받기 → 처리 정보 확인(PENDING·최신이 아니면 건너뜀) → 원본 받기 → PARSE(형식별 파서) → CHUNK(공통 청커) → VALIDATE(공통 표준 런북만)
         → EMBED → STORE(이전 조각 교체) → 결과 보고(추출 본문, 조각 수, 조항 번호)
```

단계마다 실패 사유와 다시 시도 여부를 정한다. 파일 자체의 문제(형식 불일치, 암호화, 텍스트 없는 PDF, 쪽 수 초과, 조각 0개, 조항 번호 불일치)는 다시 시도하지 않고, 임베딩 API·DB 오류는 다시 시도한다. 사유에 비밀 값이나 원문을 넣지 않는다.

### 5.3 형식별 파서 (`indexer/parse.py`, ai-web-service에서 옮김)

모두 `Parsed(text, headings=[(level, title, offset)])`를 돌려준다.

| 형식 | 라이브러리 | 본문 | 제목 |
|---|---|---|---|
| `md` | 없음 | UTF-8 원문 | `#`~`######` 줄. 코드 펜스 안의 `#`는 제목으로 보지 않는다(ai-web-service에서 알려진 한계를 고친다) |
| `txt` | 없음 | UTF-8 원문 | 없음 |
| `pdf` | `pdfplumber` | 줄 단위 텍스트, 쪽 끝에 줄바꿈 | 글자 크기로 추정한다. 본문 크기(최빈값)의 1.15배 이상이고 120자 이하인 줄. 본문 크기가 전체 글자의 과반이 아니거나 줄이 3개 미만이면 추정하지 않는다(애매하면 자르지 않는다). 그다음 `제N조`·`RB-STD-0N` 같은 번호로 시작하는 줄도 제목으로 본다(OpsLens 추가) |
| `docx` | `python-docx` | 문단과 표를 놓인 순서대로. 표는 `\| 셀 \| 셀 \|` 행 | 문단 스타일이 `Heading 1~6`(한국어 Word의 "제목 1~6" 포함) |

- 상한: PDF 200쪽. 넘으면 거절한다. 셀은 500자까지.
- 실패 사유 예: `암호화된 PDF라 본문을 추출할 수 없다`, `텍스트가 없는 PDF다 (스캔 문서는 지원하지 않는다)`, `docx를 읽을 수 없다: …`.
- docx의 머리글·바닥글·각주·텍스트 상자, PDF의 이미지는 읽지 않는다.

### 5.4 공통 청커 (`indexer/chunk.py`, ai-web-service에서 옮김)

- 제목으로 절을 나누고 절 경로를 만든다. 첫 제목 앞의 내용은 경로 없는 도입부 절이다.
- 절이 700토큰 이하면 나누지 않는다. 넘으면 줄 단위로 목표 400토큰, 겹침 60토큰으로 나눈다. 코드 블록과 표 행은 중간에서 끊지 않는다.
- 조각 앞에 헤더를 붙여 임베딩한다: `[문서] {제목} | {고객사 또는 MSP 공통} | {종류} | 절 {절 경로}`.
- 토큰은 추정값이다(한글·한자 0.7, 나머지 4자당 1).
- **지금 Markdown 문서의 결과:** 런북·SLA 조항은 700토큰보다 짧아 `## ` 조항 하나가 조각 하나다. 지금과 달라지는 점은 문서 제목(`# `)이 절 경로 앞에 붙고, 머리말이 도입부 조각으로 들어오는 것이다. 판정표 일치 테스트와 E2E로 검색 결과가 바뀌지 않는지 확인한다.

### 5.5 공통 표준 런북 검사 (VALIDATE)

- 대상: `_common`의 `runbook` 문서 중 `runbook-standard`.
- 절 경로의 제목에서 `RB-STD-\d{2}`를 모은다. 조항 번호마다 정확히 한 절이 있어야 하고, 번호 집합이 지금 검색 버전(처음이면 저장소의 시드 문서)과 같아야 한다.
- 실패 사유 예: `공통 표준 런북의 조항 번호가 바뀌었다: 빠짐 RB-STD-04, 늘어남 RB-STD-08. agent의 조치 검사가 조항 번호에 묶여 있어 색인하지 않았다`, `조항 제목을 찾지 못했다: RB-STD-03 (PDF 제목 추정 실패. Word 제목 스타일이나 Markdown으로 올린다)`.
- 실패하면 이전 버전으로 검색이 계속된다(2장).

### 5.6 테스트

- ai-web-service의 골든 방식을 따른다: `server/python/indexer/tests/golden/`에 형식별 샘플(제목 스타일 docx, 표가 있는 docx, 제목 크기가 다른 PDF, 서식이 균일한 PDF, 스캔 PDF, 암호화 PDF, 코드 펜스가 있는 md)과 기대 조각(JSON)을 둔다.
- 공통 표준 런북을 md·docx·pdf로 만든 샘플이 같은 조항 번호 집합을 낸다.
- 시드 문서 5개의 조각 경계가 지금과 같다(조항 하나 = 조각 하나).

## 6. 처음 데이터 (시드)

- `data/documents`는 저장소에 남긴다. backend가 기동할 때 `documents`에 없는 (고객사, 문서)만 버전 1로 올린다(`uploaded_by = 'seed'`). 이미 있으면 건드리지 않는다. 화면에서 올린 버전을 덮어쓰지 않기 위해서다.
- 문서 종류와 제목은 지금 파일 이름 접두어와 `# ` 제목으로 정한다(시드에만 쓰는 규칙).
- backend에 `./data/documents:/seed/documents:ro`를 마운트한다.
- `tests/test_runbook_clauses.py`는 지금처럼 저장소의 공통 런북을 본다. 화면에서 올린 새 버전은 5.5의 검사가 같은 조항 번호를 갖게 한다.

## 7. 원문 참조 (rag-mcp)

- `read_document`와 `docs://{tenant}/{doc_id}` 리소스는 backend 내부 API(4.2)로 검색 버전의 추출 본문을 받는다. Markdown은 원문 그대로(머리말 포함), PDF·Word는 파서가 뽑은 본문이다.
- `postmortem` 문서는 저장소에 없으므로 지금처럼 조각을 이어 붙인다.
- `search_documents` 결과에 `section_path`와 `source_format`, `document_version`을 더한다. 근거 인용이 "SLA 계약서 > 제5조"처럼 경로를 쓸 수 있다.

## 8. 화면

- 메뉴에 **문서**(`/documents`)를 더한다. MSP 관리자·엔지니어에게만 보인다.
- 왼쪽에 범위(공통, 고객사들), 오른쪽에 문서 표: 제목, 종류, 형식, 최신·검색 버전, 색인 상태(대기·완료·실패 단계와 사유), 올린 사람, 시각.
- **새 문서**: 파일(`.md`·`.txt`·`.pdf`·`.docx`), 범위, 종류(선택), 문서 ID, 제목. 범위 목록은 올릴 수 있는 범위만 보인다.
- 문서를 누르면 버전 이력: 버전마다 원본 받기, 추출 본문 보기, 조각 수, **이 버전으로 되돌리기**. 새 버전 올리기, 삭제.
- 최신 버전이 실패했으면 "v3 색인 실패(사유) — 검색은 v2"로 보인다.
- 권한 없는 버튼은 숨긴다. 권한 판단은 backend가 다시 한다.
- 색인 대기 중이면 몇 초마다 상태를 다시 읽는다.

## 9. 단계

1. **저장과 권한:** compose(seaweedfs, seaweedfs-init), `.env.example`, V18, `DocumentStore`(S3 호환 클라이언트, AWS SDK v2), 화면 API, 업로드 검사, 감사 로그, 시드. 테스트: 권한 표(관리자·담당 엔지니어·비담당 엔지니어·뷰어 × 공통·고객사), 업로드 검사(확장자 위장 포함), 버전·되돌리기.
2. **색인 연동 (Markdown·텍스트):** `document_outbox`와 relay, 토픽 `documents.index`, 내부 API, `ServiceTokenFilter` 경로, `document-indexer` consumer(과 `http` 모드 조회), 공통 청커, 결과 보고, 검색 버전 전환, 삭제 시 조각 삭제, 5분 점검. 테스트: outbox가 버전과 같은 트랜잭션에 쓰인다, 중복·지난 버전 메시지를 건너뛴다, 시드 문서 조각 경계가 지금과 같다, 판정표 일치 테스트, 상태 전이.
3. **형식별 파서 (PDF·Word):** `parse.py`의 pdf·docx 파서, 골든 테스트(5.6), 공통 표준 런북 검사(5.5).
4. **원문 참조:** rag-mcp `read_document`를 backend API로, 검색 결과에 절 경로. 테스트: 다른 고객사 문서 403, `_common`은 허용, postmortem은 조각.
5. **화면:** 문서 화면, API 클라이언트, 권한별 버튼, 상태 표시. 프런트 테스트.
6. **E2E와 문서:**
   - 사전 점검 "현재 버전의 공통 표준 런북이 색인됐다"를 "공통 표준 런북의 검색 버전이 `INDEXED`다"로 바꾼다.
   - 새 시나리오 `documents`: 엔지니어가 담당 고객사 SLA 계약서를 docx로 새 버전 업로드 → 30초 안에 `INDEXED` → 그 고객사 작업의 문서 검색에 새 조항이 절 경로와 함께 나온다 → 원래 버전으로 되돌린다. 엔지니어가 공통 문서를 올리면 403. 관리자가 조항 번호를 뺀 공통 표준 런북 PDF를 올리면 `FAILED`이고 검색은 이전 버전이다.
   - README(새 변수, 문서 관리), 기획안(데이터 흐름, 내부 API 표), 기능별-변경사항.

## 10. 완료 기준

- 업로드한 문서가 저장소에 버전별로 남고, 새 버전을 올려도 이전 버전의 원본과 추출 본문을 받을 수 있다
- 권한 표대로 허용·거부된다: 공통은 관리자만, 고객사 문서는 관리자와 담당 엔지니어만 바꾼다. 비담당 엔지니어와 뷰어는 403 (backend 테스트)
- `md`·`txt`·`pdf`·`docx`가 색인된다. docx 제목 스타일과 PDF 제목 추정이 절 경로가 되고, 표는 행 단위로 들어간다 (골든 테스트)
- 공통 표준 런북을 docx·pdf로 올려도 조항 번호가 같으면 색인되고, 다르면 `FAILED`(사유)이며 검색은 이전 버전이다
- 업로드 요청은 파일을 해석하거나 OpenAI를 부르지 않는다. 임베딩 API가 실패해도 업로드는 성공하고 상태가 `FAILED`(다시 시도 가능)가 되며, 다음 주기에 다시 시도된다
- 올린 뒤 30초 안에 `INDEXED`가 되고 agent의 문서 검색에 반영된다 (E2E `documents`, `DISPATCH_MODE=kafka`)
- 같은 메시지를 두 번 받거나 지난 버전의 메시지가 늦게 와도 한 번만 임베딩하고, 최신 버전을 덮어쓰지 않는다
- Kafka가 멈췄다 돌아와도 대기 중이던 버전이 색인된다(점검이 다시 보낸다). `DISPATCH_MODE=http`에서도 색인된다
- 되돌리기는 새 버전을 만들고, 검색 결과가 되돌린 내용으로 바뀐다
- 삭제하면 검색에 나오지 않고, 저장소의 버전 파일은 남는다
- 시드 문서의 조각 경계가 지금과 같고(조항 하나 = 조각 하나), 판정표 일치 테스트가 통과한다
- `read_document`가 Markdown은 원문 그대로(머리말 포함), PDF·Word는 추출 본문을 돌려주고, 다른 고객사 문서는 403
- 저장소의 `data/documents`를 고치지 않아도 문서를 바꿀 수 있고, 시드는 화면에서 올린 버전을 덮어쓰지 않는다
- 전체 E2E 통과

## 11. 하지 않는 것

- 스캔 PDF(OCR), 이미지 안의 글자
- `pptx`·`xlsx`·`hwp`·옛 `doc` (파서 구조상 ai-web-service의 pptx·xlsx 파서를 옮기면 더할 수 있다)
- 문서 승인 절차(올리면 바로 반영된다. 감사 로그로 추적한다)
- 문서 단위 세부 권한(고객사 단위만)
- 완전 삭제(버전 파일 제거)
- 고객사 사용자(TENANT_VIEWER)의 문서 열람·업로드
- 회고(postmortem) 색인을 공통 청커로 바꾸는 것
- 저장소 버킷 버전 관리·수명 주기 정책, 백업

## 12. 정할 것 (승인 때 확인)

- 새 비밀 값 2개(3.1). `.env`는 사용자가 채운다.
- 새 의존성: backend에 AWS SDK v2 S3 클라이언트, indexer에 `aiokafka`·`pdfplumber`·`python-docx`, compose에 SeaweedFS 이미지(`chrislusf/seaweedfs:4.48`).
- (결정) 색인 요청 전달은 Kafka + Transactional Outbox(2장). 새 토픽 `documents.index`.
- PDF 제목 추정에 번호 패턴(`제N조`, `RB-STD-0N`)을 더하는 것(5.3). ai-web-service는 글자 크기만 본다.

-- 근거 문서 저장소 (SPEC-document-store 3.2). 원본은 S3 저장소(SeaweedFS)에 버전별로 두고, 여기에는 메타데이터와 색인 상태를 둔다.
-- 검색 조각(docs.chunks)은 indexer가 만든다. 검색에 쓰는 버전은 마지막으로 색인에 성공한 버전(active_version)이다.

CREATE TABLE documents (
    id                UUID        PRIMARY KEY,
    tenant_id         TEXT        NOT NULL,          -- 고객사 ID 또는 '_common'(MSP 공통)
    doc_id            TEXT        NOT NULL,          -- 검색 결과의 문서 id, uri의 마지막 부분
    doc_type          TEXT        NOT NULL,          -- runbook | sla
    title             TEXT        NOT NULL,
    active_version    INT,                           -- 검색에 쓰는 버전. 색인에 성공한 적이 없으면 NULL
    deleted_at        TIMESTAMPTZ,
    deleted_by        TEXT,
    chunks_removed_at TIMESTAMPTZ,                   -- 삭제한 문서의 조각을 indexer가 지운 시각
    created_by        TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id)
);

CREATE TABLE document_versions (
    id                UUID        PRIMARY KEY,
    document_id       UUID        NOT NULL REFERENCES documents (id),
    version           INT         NOT NULL,
    original_filename TEXT        NOT NULL,
    format            TEXT        NOT NULL,          -- md | txt | pdf | docx
    object_key        TEXT        NOT NULL,          -- 원본. 추출 본문은 같은 경로의 extracted.md
    sha256            TEXT        NOT NULL,
    size_bytes        INT         NOT NULL,
    restored_from     INT,                           -- 되돌리기로 만든 버전이면 원래 버전 번호
    uploaded_by       TEXT        NOT NULL,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    index_status      TEXT        NOT NULL DEFAULT 'PENDING',   -- PENDING | INDEXED | FAILED | SUPERSEDED
    index_stage       TEXT,                          -- 실패한 단계: PARSE | CHUNK | VALIDATE | EMBED | STORE
    index_error       TEXT,
    retryable         BOOLEAN,
    attempts          INT         NOT NULL DEFAULT 0,
    last_attempt_at   TIMESTAMPTZ,
    chunk_count       INT,
    clauses           TEXT[],                        -- 공통 표준 런북에서 찾은 조항 번호 (VALIDATE의 기준)
    has_extracted     BOOLEAN     NOT NULL DEFAULT false,
    indexed_at        TIMESTAMPTZ,
    UNIQUE (document_id, version)
);

CREATE INDEX document_versions_waiting ON document_versions (uploaded_at)
    WHERE index_status IN ('PENDING', 'FAILED');

-- 색인 요청 전달 대기열 (Transactional Outbox). task_outbox와 같은 모양이다
CREATE TABLE document_outbox (
    id                  BIGSERIAL   PRIMARY KEY,
    document_id         UUID        NOT NULL REFERENCES documents (id),   -- Kafka 키
    document_version_id UUID        REFERENCES document_versions (id),     -- DELETE면 NULL
    kind                TEXT        NOT NULL,                     -- INDEX | DELETE
    status              TEXT        NOT NULL DEFAULT 'PENDING',   -- PENDING | SENT | FAILED
    attempts            INT         NOT NULL DEFAULT 0,
    last_error          TEXT,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ
);

CREATE INDEX document_outbox_pending ON document_outbox (next_attempt_at, id) WHERE status = 'PENDING';

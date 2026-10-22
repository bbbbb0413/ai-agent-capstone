-- 파일 본문은 작업 전달 메시지/Kafka에 넣지 않고 권한 검사 후 내부 API로 읽는다.
CREATE TABLE task_attachments (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES ai_tasks(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content BYTEA NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 4194304),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX task_attachments_task ON task_attachments(task_id, created_at);

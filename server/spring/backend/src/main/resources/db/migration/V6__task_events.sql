-- 진행 이벤트 이력. 끝난 작업을 열어도 진행 과정(단계, 도구, 승인)을 다시 보여 준다.
-- TOKEN(답변 조각)은 양이 많고 결과(answer)에 이미 있으므로 저장하지 않는다.
CREATE TABLE task_events (
    id         BIGSERIAL PRIMARY KEY,
    task_id    UUID        NOT NULL REFERENCES ai_tasks (id),
    type       TEXT        NOT NULL,          -- TOOL_START | TOOL_END | APPROVAL_REQUIRED | APPROVAL_DECIDED | CHANGE_PENDING | CHANGE_FINISHED | DONE | FAILED
    data       JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX task_events_task ON task_events (task_id, id);

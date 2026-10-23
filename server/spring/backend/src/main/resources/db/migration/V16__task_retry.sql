-- 실패한 작업 다시 실행 (SPEC-limitations-fixes 3단계 4.2의 6). 원래 작업은 FAILED로 남기고 새 작업에 원래 작업을 남긴다.
-- 같은 작업을 두 번 다시 실행하지 못하게 유일 인덱스를 둔다.
ALTER TABLE ai_tasks ADD COLUMN retry_of UUID REFERENCES ai_tasks (id);
CREATE UNIQUE INDEX ai_tasks_retry_of ON ai_tasks (retry_of) WHERE retry_of IS NOT NULL;

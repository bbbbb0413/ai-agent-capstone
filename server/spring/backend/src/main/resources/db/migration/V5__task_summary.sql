-- 작업 결과의 정형 요약 (에러 유형, 원인, 조치, 승인, 회복 여부). 화면 요약 표가 쓴다.
ALTER TABLE ai_tasks ADD COLUMN IF NOT EXISTS summary JSONB;

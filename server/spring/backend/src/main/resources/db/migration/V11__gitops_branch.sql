-- 고객사 설정 저장소의 브랜치 (SPEC-fault-based-actions 5.8, 결정 4). PR의 base와 커밋 조회 기준이다.
-- 평소에는 main이다. E2E는 실행 동안 임시 브랜치(e2e/<시각>)로 바꿨다가 끝나면 main으로 되돌린다.
ALTER TABLE tenants ADD COLUMN gitops_branch TEXT NOT NULL DEFAULT 'main';

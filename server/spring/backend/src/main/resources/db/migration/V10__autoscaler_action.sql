-- A사 order-api는 HPA가 레플리카를 관리한다 (SPEC-fault-based-actions 5.4.2, 결정 8).
-- HPA 최대치가 모자라면 MSP가 안전 상한까지 최대치를 올리는 PR을 만든다. 계약 허용 조치에 추가한다.
UPDATE tenants SET allowed_actions = array_append(allowed_actions, 'adjust_autoscaler')
WHERE id = 'tenant-a' AND NOT ('adjust_autoscaler' = ANY (allowed_actions));

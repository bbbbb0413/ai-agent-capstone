package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * AI 작업 한 건. status: QUEUED → RUNNING → (WAITING_APPROVAL | CHANGE_PENDING ↔ RUNNING) → DONE | FAILED
 * CHANGE_PENDING: GitOps PR의 병합과 ArgoCD 동기화를 기다린다. pendingAction에 PR 정보가 있다.
 * retryOf: 이 작업이 다시 실행한 원래 작업. retriedBy: 이 작업을 다시 실행한 새 작업 (없으면 null)
 */
public record TaskRecord(
        UUID id,
        String tenantId,
        Long incidentId,
        String type,
        String question,
        String status,
        Long requestedBy,
        JsonNode pendingAction,
        String answer,
        JsonNode sources,
        JsonNode summary,
        String traceUrl,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        UUID retryOf,
        UUID retriedBy) {

    public static final String QUEUED = "QUEUED";
    public static final String RUNNING = "RUNNING";
    public static final String WAITING_APPROVAL = "WAITING_APPROVAL";
    public static final String CHANGE_PENDING = "CHANGE_PENDING";
    public static final String DONE = "DONE";
    public static final String FAILED = "FAILED";
}

package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.OffsetDateTime;
import java.util.UUID;

/** AI 작업 한 건. status: QUEUED → RUNNING → (WAITING_APPROVAL ↔ RUNNING) → DONE | FAILED */
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
        String traceUrl,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt) {

    public static final String QUEUED = "QUEUED";
    public static final String RUNNING = "RUNNING";
    public static final String WAITING_APPROVAL = "WAITING_APPROVAL";
    public static final String DONE = "DONE";
    public static final String FAILED = "FAILED";
}

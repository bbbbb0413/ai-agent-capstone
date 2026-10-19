package com.opslens.backend.task;

import java.util.UUID;

/** backend → agent로 넘기는 작업. HTTP 본문이나 Kafka 메시지 값이 된다. */
public record TaskMessage(
        UUID taskId,
        String tenantId,
        String slaTier,
        Long incidentId,
        String type,
        String question,
        String traceId) {
}

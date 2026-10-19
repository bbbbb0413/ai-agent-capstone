package com.opslens.backend.incident;

import java.time.OffsetDateTime;

public record Incident(
        long id,
        String tenantId,
        String service,
        String title,
        String severity,
        String status,
        String report,
        OffsetDateTime startedAt,
        OffsetDateTime resolvedAt) {
}

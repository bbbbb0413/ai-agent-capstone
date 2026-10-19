package com.opslens.backend.tenant;

import java.math.BigDecimal;
import java.util.List;

public record Tenant(
        String id,
        String name,
        String slaTier,
        BigDecimal availabilityTarget,
        int responseMinutes,
        String appUrl,
        List<String> allowedActions) {
}

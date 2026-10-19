package com.opslens.backend.tenant;

import java.time.Duration;
import java.time.OffsetDateTime;
import org.springframework.stereotype.Component;

/**
 * SLA 업무 규칙. 응답 기한과 크레딧 계산은 여기 한 곳에서만 한다.
 * Agent와 MCP Server는 이 결과를 받아 쓰기만 한다 (업무 규칙 복제 금지).
 */
@Component
public class SlaPolicy {

    public record SlaTerms(String tier, String availabilityTarget, int responseMinutes,
            OffsetDateTime responseDeadline, long remainingMinutes) {}

    public record SlaCredit(String tier, long downtimeMinutes, int creditPercent) {}

    public SlaTerms terms(Tenant tenant, OffsetDateTime incidentStartedAt) {
        OffsetDateTime deadline = incidentStartedAt.plusMinutes(tenant.responseMinutes());
        long remaining = Math.max(0, Duration.between(OffsetDateTime.now(), deadline).toMinutes());
        return new SlaTerms(tenant.slaTier(), tenant.availabilityTarget().toPlainString() + "%",
                tenant.responseMinutes(), deadline, remaining);
    }

    // 데모 계약서(data/documents/{tenant}/sla-contract.md) 제4조와 같은 기준이다
    public SlaCredit credit(Tenant tenant, OffsetDateTime startedAt, OffsetDateTime resolvedAt) {
        OffsetDateTime end = resolvedAt != null ? resolvedAt : OffsetDateTime.now();
        long downtime = Duration.between(startedAt, end).toMinutes();
        int percent = switch (tenant.slaTier()) {
            case "GOLD" -> downtime > 60 ? 25 : downtime > 15 ? 10 : 0;
            case "SILVER" -> downtime > 240 ? 10 : downtime > 60 ? 5 : 0;
            default -> 0;
        };
        return new SlaCredit(tenant.slaTier(), downtime, percent);
    }
}

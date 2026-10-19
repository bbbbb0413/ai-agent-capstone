package com.opslens.backend.tenant;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class SlaPolicyTest {
    @ParameterizedTest
    @CsvSource({"GOLD,P1,15", "GOLD,P2,60", "SILVER,P1,60", "SILVER,P2,240"})
    void deadlineMatchesSeverityInContract(String tier, String severity, int minutes) {
        Tenant tenant = new Tenant("test", "test", tier, new BigDecimal("99.9"),
                "GOLD".equals(tier) ? 15 : 60, List.of(), "DIRECT", "test", null);
        OffsetDateTime started = OffsetDateTime.parse("2026-10-19T00:00:00Z");
        SlaPolicy.SlaTerms terms = new SlaPolicy().terms(tenant, severity, started);
        assertEquals(minutes, terms.responseMinutes());
        assertEquals(started.plusMinutes(minutes), terms.responseDeadline());
    }
}

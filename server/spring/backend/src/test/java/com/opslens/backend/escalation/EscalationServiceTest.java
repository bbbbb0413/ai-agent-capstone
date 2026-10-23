package com.opslens.backend.escalation;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.opslens.backend.change.ServiceCatalog;
import com.opslens.backend.incident.Incident;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** 받는 사람 결정과 메시지 작성 (발송 직전까지). 공통 런북 RB-STD-04~07과 같은 기준이다. */
class EscalationServiceTest {
    private final EscalationRepository repository = mock(EscalationRepository.class);
    private final TenantRepository tenants = mock(TenantRepository.class);
    private final ServiceCatalog catalog = mock(ServiceCatalog.class);
    private final Tenant tenantA = new Tenant("tenant-a", "A사", "GOLD", new BigDecimal("99.90"), 15,
            List.of("restart_pods", "scale_deployment", "rollback_deployment", "notice"), "GITOPS", "tenant-a",
            "gitops/tenants/tenant-a");
    private final Tenant tenantB = new Tenant("tenant-b", "B사", "SILVER", new BigDecimal("99.50"), 60,
            List.of("restart_pods", "notice"), "DIRECT", "tenant-b", null);
    private final ServiceCatalog.Entry orderApi = new ServiceCatalog.Entry("tenant-a", "order-api", "kubernetes",
            "kind", "tenant-a", false, 2, 6, 20, List.of(new ServiceCatalog.Dependency("postgres", "database",
                    "tenant", "A사 DBA팀", 100, new BigDecimal("0.80"))), null, null);
    private final ServiceCatalog.Entry reservationApi = new ServiceCatalog.Entry("tenant-b", "reservation-api",
            "kubernetes", "kind", "tenant-b", false, 1, 1, 20, List.of(new ServiceCatalog.Dependency(
                    "payment-gateway", "external_api", "third_party", "B사 결제 담당", null, new BigDecimal("0.80"))), null, null);
    private final Incident incidentA = incident(1, "tenant-a", "order-api");
    private final Incident incidentB = incident(2, "tenant-b", "reservation-api");
    private EscalationService service;

    @BeforeEach
    void setup() {
        when(tenants.oncall(any())).thenReturn(List.of(new TenantRepository.Contact("admin", "MSP_ADMIN"),
                new TenantRepository.Contact("eng-a", "MSP_ENGINEER")));
        when(tenants.customerContact("tenant-b")).thenReturn(Optional.of("B사 운영 승인 담당"));
        when(catalog.require(tenantA, "order-api")).thenReturn(orderApi);
        when(catalog.require(tenantB, "reservation-api")).thenReturn(reservationApi);
        when(catalog.list("tenant-a")).thenReturn(List.of(orderApi));
        when(catalog.list("tenant-b")).thenReturn(List.of(reservationApi));
        when(repository.findQueued(anyLong(), any())).thenReturn(Optional.empty());
        when(repository.insert(any(), anyLong(), any(), any(), any(), any())).thenReturn(7L);
        service = new EscalationService(repository, tenants, catalog);
    }

    @Test
    void blockedActionGoesToOncallAndCustomer() {
        Map<String, Object> result = service.queue(tenantB, incidentB, new EscalationService.Request(
                "action_blocked", "확장이 필요하지만 계약상 불가", null, "scale_deployment", "contract", null), null);
        assertEquals(Escalation.QUEUED, result.get("status"));
        assertEquals(List.of("MSP_ONCALL", "MSP_ONCALL", "CUSTOMER"), kinds(result));
        String message = (String) result.get("message");
        assertTrue(message.contains("필요한 조치: scale_deployment"), message);
        assertTrue(message.contains("계약상 허용되지 않는 조치"), message);
    }

    @Test
    void dependencyOwnerIsAddedFromCatalog() {
        Map<String, Object> result = service.queue(tenantA, incidentA, new EscalationService.Request(
                "DEPENDENCY", "DB 응답 지연", "order-api", null, null, "postgres"), null);
        @SuppressWarnings("unchecked")
        List<Escalation.Recipient> recipients = (List<Escalation.Recipient>) result.get("recipients");
        assertEquals(new Escalation.Recipient("DEPENDENCY_OWNER", "A사 DBA팀", "postgres"), recipients.get(2));
    }

    @Test
    void thirdPartyDependencyIsANoticeNotAnEscalation() {
        ResponseStatusException e = assertThrows(ResponseStatusException.class, () -> service.queue(tenantB,
                incidentB, new EscalationService.Request("DEPENDENCY", "결제 지연", null, null, null,
                        "payment-gateway"), null));
        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, e.getStatusCode());
        assertTrue(e.getReason().contains("고객 공지"), e.getReason());
        verify(repository, never()).insert(any(), anyLong(), any(), any(), any(), any());
    }

    @Test
    void blockedActionNeedsTheActionAndTheReason() {
        assertThrows(ResponseStatusException.class, () -> service.queue(tenantB, incidentB,
                new EscalationService.Request("ACTION_BLOCKED", "불가", null, null, "CONTRACT", null), null));
        assertThrows(ResponseStatusException.class, () -> service.queue(tenantB, incidentB,
                new EscalationService.Request("ACTION_BLOCKED", "불가", null, "rollback_deployment", "BUDGET", null),
                null));
    }

    @Test
    void queuedDuplicateIsReturnedInsteadOfAddingAnother() {
        Escalation queued = new Escalation(3, "tenant-a", 1, "P1", "P1 장애 발생", "order-api", null, null, null,
                List.of(), "m", Escalation.QUEUED, OffsetDateTime.now(), null);
        when(repository.findQueued(eq(1L), any())).thenReturn(Optional.of(queued));
        Map<String, Object> result = service.queue(tenantA, incidentA,
                new EscalationService.Request("P1", "P1 장애 발생", null, null, null, null), null);
        assertEquals(3L, result.get("escalationId"));
        assertEquals(true, result.get("duplicate"));
        verify(repository, never()).insert(any(), anyLong(), any(), any(), any(), any());
    }

    private static Incident incident(long id, String tenant, String service) {
        return new Incident(id, tenant, service, "5xx 급증", "P2", "OPEN", null, OffsetDateTime.now(), null);
    }

    @SuppressWarnings("unchecked")
    private static List<String> kinds(Map<String, Object> result) {
        return ((List<Escalation.Recipient>) result.get("recipients")).stream().map(Escalation.Recipient::kind)
                .toList();
    }
}

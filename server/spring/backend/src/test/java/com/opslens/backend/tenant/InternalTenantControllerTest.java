package com.opslens.backend.tenant;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.ServiceTokenFilter;
import com.opslens.backend.change.ChangeService;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.task.TaskRecord;
import com.opslens.backend.task.TaskTokens;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.server.ResponseStatusException;

/** 내부 업무 API의 고객사는 작업 토큰에서 정한다. 헤더나 경로의 고객사를 믿지 않는다. */
class InternalTenantControllerTest {
    private final IncidentRepository incidents = mock(IncidentRepository.class);
    private final TenantRepository tenants = mock(TenantRepository.class);
    private final ChangeService changes = mock(ChangeService.class);
    private final AuditLog audit = mock(AuditLog.class);
    private final TaskTokens tokens = new TaskTokens();
    private final InternalTenantController controller =
            new InternalTenantController(tenants, incidents, null, changes, null, null, audit, tokens);

    private String tokenFor(String tenant) {
        return tokens.issue(new TaskRecord(UUID.randomUUID(), tenant, null, "QUESTION", "q", TaskRecord.RUNNING, null,
                null, null, null, null, null, OffsetDateTime.now(), OffsetDateTime.now(), null, null)).token();
    }

    @Test
    void taskTokenOfAnotherTenantIsBlockedAndAudited() {
        var error = assertThrows(ResponseStatusException.class,
                () -> controller.services("tenant-a", tokenFor("tenant-b")));
        assertEquals(HttpStatus.FORBIDDEN, error.getStatusCode());
        verify(audit).record(eq("tenant-b"), eq("agent"), eq("TENANT_ISOLATION_BLOCKED"), any(), isNull());
        verifyNoInteractions(changes);
    }

    @Test
    void missingOrForgedTaskTokenIsUnauthorized() {
        for (String token : new String[] {null, "", "not-a-jwt", new TaskTokens().issue(new TaskRecord(UUID.randomUUID(),
                "tenant-a", null, "QUESTION", "q", TaskRecord.RUNNING, null, null, null, null, null, null,
                OffsetDateTime.now(), OffsetDateTime.now(), null, null)).token()}) {   // 마지막: 다른 키로 서명
            var error = assertThrows(ResponseStatusException.class, () -> controller.services("tenant-a", token));
            assertEquals(HttpStatus.UNAUTHORIZED, error.getStatusCode());
        }
        verifyNoInteractions(changes);
    }

    @Test
    void ownTenantTokenReadsData() {
        when(tenants.get("tenant-a")).thenReturn(mock(Tenant.class));
        when(changes.inventory(any())).thenReturn(List.of());
        assertEquals(List.of(), controller.services("tenant-a", tokenFor("tenant-a")));
    }

    @Test
    void postmortemsAreOnlyForTheIndexer() {
        var request = new MockHttpServletRequest();
        request.setAttribute(ServiceTokenFilter.CALLER, "mcp");
        assertEquals(HttpStatus.FORBIDDEN, assertThrows(ResponseStatusException.class,
                () -> controller.postmortems("tenant-a", request, 0)).getStatusCode());
        verifyNoInteractions(incidents);
        request.setAttribute(ServiceTokenFilter.CALLER, "indexer");
        controller.postmortems("tenant-a", request, 20);
        verify(incidents).postmortems("tenant-a", 20);
    }
}

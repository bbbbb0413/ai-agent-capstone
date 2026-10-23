package com.opslens.backend.tenant;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.incident.IncidentRepository;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

class PostmortemControllerTest {
    @Test
    void tenantHeaderMismatchIsRejectedBeforeReadingReports() {
        var incidents = mock(IncidentRepository.class);
        var tenants = mock(TenantRepository.class);
        var controller = new InternalTenantController(tenants, incidents, null, null, null, null,
                mock(AuditLog.class));
        var error = assertThrows(ResponseStatusException.class,
                () -> controller.postmortems("tenant-b", "tenant-a", 0));
        assertEquals(HttpStatus.FORBIDDEN, error.getStatusCode());
        verifyNoInteractions(incidents, tenants);
        controller.postmortems("tenant-a", "tenant-a", 20);
        verify(incidents).postmortems("tenant-a", 20);
    }
}

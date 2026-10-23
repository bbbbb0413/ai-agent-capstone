package com.opslens.backend.impact;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.change.ServiceCatalog;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

class ImpactServiceTest {
    private final ServiceCatalog catalog = mock(ServiceCatalog.class);
    private final ImpactMetrics metrics = mock(ImpactMetrics.class);
    private final ImpactService service = new ImpactService(catalog, metrics);
    private final CurrentUser staff = new CurrentUser(1, "engineer", "MSP_ENGINEER", List.of("tenant-a"));

    static ServiceCatalog.Dependency dep(String name, String kind) {
        return new ServiceCatalog.Dependency(name, kind, "tenant", null, null, null);
    }
    static ServiceCatalog.Entry entry(String name, ServiceCatalog.Dependency... dependencies) {
        return new ServiceCatalog.Entry("tenant-a", name, "kubernetes", null, null, false, 1, 3, null, List.of(dependencies), null, null);
    }
    static Map<ImpactMetrics.Key, Double> values(String... pairs) {
        var result = new HashMap<ImpactMetrics.Key, Double>();
        for (int i = 0; i < pairs.length; i += 3)
            result.put(new ImpactMetrics.Key(pairs[i], pairs[i+1]), Double.valueOf(pairs[i+2]));
        return result;
    }

    @Test void findsEverySharedDependencyConsumerAndIndirectPathWithoutCycle() {
        var db = dep("postgres", "database");
        var entries = List.of(entry("order", db, dep("web", "internal_service")), entry("product", db),
                entry("web", dep("order", "internal_service")), entry("checkout", dep("web", "internal_service")),
                entry("unrelated", dep("postgres", "external_api")));
        var result = ImpactService.consumers("database:postgres", entries);
        assertEquals(List.of("order", "product", "web", "checkout"), result.stream().map(ImpactService.Consumer::service).toList());
        assertEquals(List.of("checkout", "web", "order", "postgres"), result.get(3).path());
        assertTrue(result.get(1).direct());
        assertFalse(result.get(2).direct());
        assertEquals(List.of("web", "checkout"), ImpactService.consumers("internal_service:order", entries)
                .stream().map(ImpactService.Consumer::service).toList());
    }

    @Test void separatesPotentialImpactFromObservedSymptomsAndPerCallerDependencyLatency() {
        when(catalog.list("tenant-a")).thenReturn(List.of(entry("order", dep("postgres", "database")),
                entry("product", dep("postgres", "database")), entry("batch")));
        when(metrics.read(eq("tenant-a"), any(Instant.class))).thenReturn(new ImpactMetrics.Snapshot(
                values("order", "", "10", "product", "", "20", "batch", "", "0", "foreign", "", "9"),
                values("order", "", "2", "product", "", "0"), values("order", "", "1.5", "product", "", "0.2"),
                values("order", "postgres", "10", "product", "postgres", "20"),
                values("order", "postgres", "1.2", "product", "postgres", "0.01"), List.of()));
        var view = service.view("tenant-a", staff);
        assertEquals(List.of("ABNORMAL", "HEALTHY", "UNKNOWN"), view.services().stream().map(ImpactService.ServiceNode::status).toList());
        assertEquals(List.of("DEGRADED", "HEALTHY"), view.edges().stream().map(ImpactService.Edge::status).toList());
        assertEquals(2, view.dependencies().getFirst().consumers().size());
        assertEquals("DEGRADED", view.dependencies().getFirst().status());
        assertEquals(0.2, view.services().getFirst().errorRate());
        assertEquals(3, view.services().size()); // 카탈로그 밖의 응답은 노드로 추가하지 않는다
    }

    @Test void rejectsForeignTenantAndViewerBeforeAnyRead() {
        assertEquals(HttpStatus.FORBIDDEN, assertThrows(ResponseStatusException.class,
                () -> service.view("tenant-b", staff)).getStatusCode());
        assertThrows(ResponseStatusException.class, () -> service.view("tenant-a",
                new CurrentUser(2, "viewer", "TENANT_VIEWER", List.of("tenant-a"))));
        verifyNoInteractions(catalog, metrics);
    }

    @Test void missingOrNonfiniteMetricsNeverBecomeHealthyAndThresholdsAreInclusive() {
        assertEquals("UNKNOWN", ImpactService.serviceStatus(10.0, null, 0.2));
        assertEquals("UNKNOWN", ImpactService.serviceStatus(0.0, 0.0, 0.0));
        assertEquals("UNKNOWN", ImpactService.serviceStatus(10.0, 0.0, Double.NaN));
        assertEquals("ABNORMAL", ImpactService.serviceStatus(10.0, 0.05, null));
        assertEquals("ABNORMAL", ImpactService.serviceStatus(10.0, null, 1.0));
        assertEquals("HEALTHY", ImpactService.serviceStatus(10.0, 0.049, 0.999));
        assertEquals("UNKNOWN", ImpactService.dependencyStatus(0.0, 2.0));
        assertEquals("DEGRADED", ImpactService.dependencyStatus(1.0, 0.3));
        assertEquals("HIGH_LATENCY", ImpactService.dependencyStatus(1.0, 3.0));
        assertEquals("UNKNOWN", ImpactService.aggregate(List.of("HEALTHY", "UNKNOWN")));
        assertEquals("DEGRADED", ImpactService.aggregate(List.of("DEGRADED", "UNKNOWN")));
    }

    @Test void emptyCatalogDoesNotCallMonitoring() {
        when(catalog.list("tenant-a")).thenReturn(List.of());
        assertTrue(service.view("tenant-a", staff).services().isEmpty());
        verifyNoInteractions(metrics);
    }
}

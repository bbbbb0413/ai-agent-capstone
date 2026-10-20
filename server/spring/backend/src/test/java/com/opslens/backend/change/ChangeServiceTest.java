package com.opslens.backend.change;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.opslens.backend.tenant.Tenant;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** 구조 제약(②층)과 실행 환경 선택(④층). SPEC-fault-based-actions 4.1·5.1·5.2, E2E guard-* 와 같은 기준이다. */
class ChangeServiceTest {
    private final ServiceCatalog catalog = mock(ServiceCatalog.class);
    private final RuntimeAdapter kubernetes = mock(RuntimeAdapter.class);
    private final GitOpsRepository gitops = mock(GitOpsRepository.class);
    private final Tenant tenantA = new Tenant("tenant-a", "A사", "GOLD", new BigDecimal("99.90"), 15,
            List.of("restart_pods", "scale_deployment", "rollback_deployment", "adjust_autoscaler", "notice"), "GITOPS",
            "tenant-a", "gitops/tenants/tenant-a");
    private final Tenant tenantB = new Tenant("tenant-b", "B사", "SILVER", new BigDecimal("99.50"), 60,
            List.of("restart_pods", "notice"), "DIRECT", "tenant-b", null);
    private final ServiceCatalog.Dependency postgres = new ServiceCatalog.Dependency("postgres", "database", "tenant",
            "A사 DBA팀", 100, new BigDecimal("0.80"));
    private final ServiceCatalog.Entry orderApi = new ServiceCatalog.Entry("tenant-a", "order-api", "kubernetes",
            "kind", "tenant-a", false, 2, 6, 20, List.of(postgres));
    private final ServiceCatalog.Entry batch = new ServiceCatalog.Entry("tenant-b", "settlement-batch", "docker",
            "b-batch-01", null, true, 1, 1, null, List.of());
    private ChangeService service;

    @BeforeEach
    void setup() {
        when(kubernetes.runtime()).thenReturn("kubernetes");
        when(kubernetes.supportedActions()).thenReturn(
                Set.of("restart_pods", "scale_deployment", "rollback_deployment", "adjust_autoscaler"));
        when(kubernetes.status(any(), any())).thenReturn(
                new RuntimeAdapter.Workload("order-api", 2, 2, "demo:0.1.0", Map.of("DEMO_POOL_SIZE", "20")));
        when(catalog.require(tenantA, "order-api")).thenReturn(orderApi);
        when(catalog.require(tenantB, "settlement-batch")).thenReturn(batch);
        service = new ChangeService(catalog, List.of(kubernetes), gitops);
    }

    @Test
    void safeMaxReplicasIsCappedByDatabaseConnections() {
        assertEquals(4, ChangeService.safeMaxReplicas(orderApi, 20));   // ⌊100 × 0.8 ÷ 20⌋ = 4 < 계약 6
        assertEquals(6, ChangeService.safeMaxReplicas(orderApi, 10));   // ⌊80 ÷ 10⌋ = 8 → 계약 상한 6
        assertEquals(6, ChangeService.safeMaxReplicas(orderApi, null)); // 풀 크기를 모르면 계약 상한
    }

    @Test
    void scaleAboveSafeMaxIsRejectedBeforeAnyChange() {
        ResponseStatusException e = assertThrows(ResponseStatusException.class, () ->
                service.execute(tenantA, "scale_deployment", "order-api", 6, "test", OffsetDateTime.now()));
        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, e.getStatusCode());
        assertTrue(e.getReason().contains("2~4"), e.getReason());
        verifyNoInteractions(gitops);
        verify(kubernetes, never()).scale(any(), any(), anyInt());
    }

    @Test
    void scaleWithinSafeMaxOpensPullRequestForGitopsTenant() {
        when(gitops.scale(tenantA, "order-api", 4, "test"))
                .thenReturn(new GitOpsRepository.PullRequest(9, "https://pr/9", "b", List.of("k.yaml")));
        Map<String, Object> result = service.execute(tenantA, "scale_deployment", "order-api", 4, "test",
                OffsetDateTime.now());
        assertEquals(ChangeService.PR_CREATED, result.get("status"));
        verify(kubernetes, never()).scale(any(), any(), anyInt());
    }

    @Test
    void restartGoesStraightToRuntimeEvenForGitopsTenant() {
        when(kubernetes.restart(tenantA, orderApi)).thenReturn(new RuntimeAdapter.Restart(List.of("p1", "p2"), true));
        Map<String, Object> result = service.execute(tenantA, "restart_pods", "order-api", null, "test",
                OffsetDateTime.now());
        assertEquals(ChangeService.EXECUTED, result.get("status"));
        assertEquals("ROLLING", result.get("restartMode"));
        verifyNoInteractions(gitops);
    }

    @Test
    void unsupportedRuntimeIsRejected() {
        ResponseStatusException e = assertThrows(ResponseStatusException.class, () ->
                service.execute(tenantB, "restart_pods", "settlement-batch", null, "test", OffsetDateTime.now()));
        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, e.getStatusCode());
        assertTrue(e.getReason().contains("docker"), e.getReason());
    }

    @Test
    void inventoryReportsComputedLimits() {
        when(catalog.list("tenant-a")).thenReturn(List.of(orderApi));
        Map<String, Object> item = service.inventory(tenantA).get(0);
        assertEquals(4, item.get("safeMaxReplicas"));
        assertEquals(true, item.get("zeroDowntimeRestart"));
        assertEquals(List.of("restart_pods", "rollback_deployment", "scale_deployment"), item.get("supportedActions"));
    }

    @Test
    void inventoryForUnsupportedRuntimeHasNoActions() {
        when(catalog.list("tenant-b")).thenReturn(List.of(batch));
        Map<String, Object> item = service.inventory(tenantB).get(0);
        assertEquals(false, item.get("runtimeSupported"));
        assertEquals(List.of(), item.get("supportedActions"));
    }

    // ── HPA (스펙 5.4.2) ───────────────────────────────────────

    private void withAutoscaler(int max, int desired) {
        when(kubernetes.status(any(), any())).thenReturn(new RuntimeAdapter.Workload("order-api", desired, desired,
                "demo:0.1.0", Map.of("DEMO_POOL_SIZE", "20"),
                new RuntimeAdapter.Autoscaler("order-api", 2, max, desired, desired, 50, 180)));
    }

    @Test
    void scaleIsRejectedWhenAutoscalerManagesReplicas() {
        withAutoscaler(3, 3);
        ResponseStatusException e = assertThrows(ResponseStatusException.class, () ->
                service.execute(tenantA, "scale_deployment", "order-api", 4, "test", OffsetDateTime.now()));
        assertTrue(e.getReason().contains("HPA") && e.getReason().contains("adjust_autoscaler"), e.getReason());
        verifyNoInteractions(gitops);
    }

    @Test
    void autoscalerMaxIsRaisedUpToSafeMaxByPullRequest() {
        withAutoscaler(3, 3);
        when(gitops.setAutoscalerMax(tenantA, "order-api", 4, "test"))
                .thenReturn(new GitOpsRepository.PullRequest(10, "https://pr/10", "b", List.of("hpa.yaml")));
        Map<String, Object> result = service.execute(tenantA, "adjust_autoscaler", "order-api", 4, "test",
                OffsetDateTime.now());
        assertEquals(ChangeService.PR_CREATED, result.get("status"));
        assertEquals(3, result.get("previousMaxReplicas"));
        assertThrows(ResponseStatusException.class, () ->   // 안전 상한 4를 넘는다
                service.execute(tenantA, "adjust_autoscaler", "order-api", 5, "test", OffsetDateTime.now()));
    }

    @Test
    void autoscalerAlreadyAtSafeMaxMeansEscalation() {
        withAutoscaler(4, 4);
        ResponseStatusException e = assertThrows(ResponseStatusException.class, () ->
                service.execute(tenantA, "adjust_autoscaler", "order-api", 5, "test", OffsetDateTime.now()));
        assertTrue(e.getReason().contains("에스컬레이션"), e.getReason());
    }

    @Test
    void inventoryShowsAutoscalerAndSwapsScaleForAdjust() {
        withAutoscaler(3, 3);
        when(catalog.list("tenant-a")).thenReturn(List.of(orderApi));
        Map<String, Object> item = service.inventory(tenantA).get(0);
        @SuppressWarnings("unchecked")
        Map<String, Object> autoscaling = (Map<String, Object>) item.get("autoscaling");
        assertEquals(true, autoscaling.get("atMax"));
        assertEquals(true, autoscaling.get("canRaiseMax"));
        assertEquals(List.of("adjust_autoscaler", "restart_pods", "rollback_deployment"), item.get("supportedActions"));
    }
}

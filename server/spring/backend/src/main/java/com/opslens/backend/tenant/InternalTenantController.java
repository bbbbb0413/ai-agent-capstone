package com.opslens.backend.tenant;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.change.ChangeService;
import com.opslens.backend.incident.Incident;
import com.opslens.backend.incident.IncidentRepository;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * business-mcp 도구가 호출하는 내부 API. 업무 규칙(소유권, SLA, 조치 허용)은 여기서만 판단한다.
 * 모든 요청은 X-Tenant-Id 헤더의 고객사와 경로의 고객사가 같아야 한다. 중간 단계를 믿지 않고 다시 검증한다.
 * 오류 메시지는 business-mcp가 ToolError로 모델에 그대로 넘기므로, 재시도에 필요한 정보를 담는다.
 */
@RestController
@RequestMapping("/internal/tenants/{tenantId}")
public class InternalTenantController {

    record CreateIncidentRequest(@NotBlank String service, @NotBlank String title, @NotBlank String severity) {}

    record EscalateRequest(@NotBlank String reason) {}

    record ActionRequest(@NotBlank String action, @NotBlank String service, Integer replicas,
            @NotNull Long incidentId, String reason) {}

    record NoticeRequest(@NotNull Long incidentId, @NotBlank String message) {}

    record SlaCreditRequest(@NotNull Long incidentId) {}

    private final TenantRepository tenants;
    private final IncidentRepository incidents;
    private final SlaPolicy sla;
    private final ChangeService changes;
    private final AuditLog audit;

    public InternalTenantController(TenantRepository tenants, IncidentRepository incidents, SlaPolicy sla,
            ChangeService changes, AuditLog audit) {
        this.tenants = tenants;
        this.incidents = incidents;
        this.sla = sla;
        this.changes = changes;
        this.audit = audit;
    }

    // ── 조회 도구 ──────────────────────────────────────────────

    @GetMapping
    Tenant profile(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller) {
        return tenant(tenantId, caller);
    }

    @GetMapping("/sla")
    SlaPolicy.SlaTerms slaTerms(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestParam long incidentId) {
        Tenant tenant = tenant(tenantId, caller);
        Incident incident = incidents.getForTenant(tenantId, incidentId);
        return sla.terms(tenant, incident.severity(), incident.startedAt());
    }

    @GetMapping("/services")
    List<Map<String, Object>> services(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller) {
        return changes.inventory(tenant(tenantId, caller));
    }

    @GetMapping("/changes")
    Map<String, Object> recentChanges(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestParam String service) {
        return changes.recentChanges(tenant(tenantId, caller), service);
    }

    @GetMapping("/incidents")
    List<Incident> incidents(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestParam(required = false) String status) {
        tenant(tenantId, caller);
        return incidents.list(List.of(tenantId), status);
    }

    @GetMapping("/oncall")
    List<TenantRepository.Contact> oncall(@PathVariable String tenantId,
            @RequestHeader("X-Tenant-Id") String caller) {
        tenant(tenantId, caller);
        return tenants.oncall(tenantId);
    }

    // ── 변경 도구 (Agent가 사람의 승인을 받은 뒤에만 호출한다) ─────────

    @PostMapping("/incidents")
    Map<String, Object> createIncident(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody CreateIncidentRequest request) {
        tenant(tenantId, caller);
        long id = incidents.create(tenantId, request.service(), request.title(), request.severity());
        audit.record(tenantId, "agent", "INCIDENT_CREATED", request, traceId);
        return Map.of("incidentId", id);
    }

    @PostMapping("/incidents/{incidentId}/escalate")
    Map<String, Object> escalate(@PathVariable String tenantId, @PathVariable long incidentId,
            @RequestHeader("X-Tenant-Id") String caller,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody EscalateRequest request) {
        tenant(tenantId, caller);
        incidents.getForTenant(tenantId, incidentId);
        List<TenantRepository.Contact> contacts = tenants.oncall(tenantId);
        // TODO: 실제 호출(메신저·전화) 대신 감사 로그로 남긴다
        audit.record(tenantId, "agent", "ESCALATED",
                Map.of("incidentId", incidentId, "reason", request.reason(), "contacts", contacts), traceId);
        return Map.of("notified", contacts);
    }

    @PostMapping("/actions")
    Map<String, Object> executeAction(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody ActionRequest request) {
        Tenant tenant = tenant(tenantId, caller);
        incidents.getForTenant(tenantId, request.incidentId());
        if (!tenant.allowedActions().contains(request.action())) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "'%s'는 %s 계약상 허용되지 않는다. 허용 조치: %s"
                            .formatted(request.action(), tenant.name(), tenant.allowedActions()));
        }
        String reason = "인시던트 #%d: %s".formatted(request.incidentId(),
                request.reason() == null ? "" : request.reason());
        Map<String, Object> result = changes.execute(tenant, request.action(), request.service(), request.replicas(),
                reason);
        audit.record(tenantId, "agent", ChangeService.PR_CREATED.equals(result.get("status"))
                ? "CHANGE_PR_CREATED" : "ACTION_EXECUTED", Map.of("incidentId", request.incidentId(), "result", result),
                traceId);
        return result;
    }

    @PostMapping("/notices")
    Map<String, Object> sendNotice(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody NoticeRequest request) {
        tenant(tenantId, caller);
        incidents.getForTenant(tenantId, request.incidentId());
        // TODO: 실제 발송(메일·메신저) 대신 감사 로그로 남기고 고객사 포털에 표시한다
        audit.record(tenantId, "agent", "NOTICE_SENT", request, traceId);
        return Map.of("sent", true);
    }

    @PostMapping("/sla-credits")
    SlaPolicy.SlaCredit confirmSlaCredit(@PathVariable String tenantId, @RequestHeader("X-Tenant-Id") String caller,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody SlaCreditRequest request) {
        Tenant tenant = tenant(tenantId, caller);
        Incident incident = incidents.getForTenant(tenantId, request.incidentId());
        SlaPolicy.SlaCredit credit = sla.credit(tenant, incident.startedAt(), incident.resolvedAt());
        audit.record(tenantId, "agent", "SLA_CREDIT_CONFIRMED",
                Map.of("incidentId", incident.id(), "credit", credit), traceId);
        return credit;
    }

    private Tenant tenant(String tenantId, String caller) {
        if (!tenantId.equals(caller)) {
            audit.record(caller, "agent", "TENANT_ISOLATION_BLOCKED", Map.of("requested", tenantId), null);
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "현재 작업의 고객사(%s)가 아닌 고객사(%s) 데이터는 조회할 수 없다".formatted(caller, tenantId));
        }
        return tenants.get(tenantId);
    }
}

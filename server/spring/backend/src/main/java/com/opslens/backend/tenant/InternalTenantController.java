package com.opslens.backend.tenant;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.ServiceTokenFilter;
import com.opslens.backend.change.ChangeService;
import com.opslens.backend.change.DependencyHealth;
import com.opslens.backend.escalation.EscalationService;
import com.opslens.backend.incident.Incident;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.task.TaskTokens;
import jakarta.servlet.http.HttpServletRequest;
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
 * 모든 요청은 작업 토큰(X-Task-Token)의 고객사와 경로의 고객사가 같아야 한다. 헤더의 고객사(X-Tenant-Id)는 믿지 않는다.
 * 오류 메시지는 business-mcp가 ToolError로 모델에 그대로 넘기므로, 재시도에 필요한 정보를 담는다.
 */
@RestController
@RequestMapping("/internal/tenants/{tenantId}")
public class InternalTenantController {

    record CreateIncidentRequest(@NotBlank String service, @NotBlank String title, @NotBlank String severity) {}

    /** category와 나머지 필드는 EscalationService가 검사한다 */
    record EscalateRequest(@NotBlank String category, @NotBlank String reason, String service, String requiredAction,
            String blockedBy, String dependency) {}

    record ActionRequest(@NotBlank String action, @NotBlank String service, Integer replicas,
            @NotNull Long incidentId, String reason) {}

    record NoticeRequest(@NotNull Long incidentId, @NotBlank String message) {}

    record SlaCreditRequest(@NotNull Long incidentId) {}

    private final TenantRepository tenants;
    private final IncidentRepository incidents;
    private final SlaPolicy sla;
    private final ChangeService changes;
    private final EscalationService escalations;
    private final DependencyHealth dependencies;
    private final AuditLog audit;
    private final TaskTokens taskTokens;

    public InternalTenantController(TenantRepository tenants, IncidentRepository incidents, SlaPolicy sla,
            ChangeService changes, EscalationService escalations, DependencyHealth dependencies, AuditLog audit,
            TaskTokens taskTokens) {
        this.tenants = tenants;
        this.incidents = incidents;
        this.sla = sla;
        this.changes = changes;
        this.escalations = escalations;
        this.dependencies = dependencies;
        this.audit = audit;
        this.taskTokens = taskTokens;
    }

    // ── 조회 도구 ──────────────────────────────────────────────

    @GetMapping
    Tenant profile(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken) {
        return tenant(tenantId, taskToken);
    }

    @GetMapping("/sla")
    SlaPolicy.SlaTerms slaTerms(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestParam long incidentId) {
        Tenant tenant = tenant(tenantId, taskToken);
        Incident incident = incidents.getForTenant(tenantId, incidentId);
        return sla.terms(tenant, incident.severity(), incident.startedAt());
    }

    @GetMapping("/services")
    List<Map<String, Object>> services(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken) {
        return changes.inventory(tenant(tenantId, taskToken));
    }

    @GetMapping("/changes")
    Map<String, Object> recentChanges(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestParam String service) {
        return changes.recentChanges(tenant(tenantId, taskToken), service);
    }

    @GetMapping("/dependencies/{name}/health")
    Map<String, Object> dependencyHealth(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @PathVariable String name) {
        return dependencies.health(tenant(tenantId, taskToken), name);
    }

    @GetMapping("/incidents")
    List<Incident> incidents(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestParam(required = false) String status) {
        tenant(tenantId, taskToken);
        return incidents.list(List.of(tenantId), status);
    }

    @GetMapping("/oncall")
    List<TenantRepository.Contact> oncall(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken) {
        tenant(tenantId, taskToken);
        return tenants.oncall(tenantId);
    }

    /**
     * postmortem-indexer가 고객사마다 부른다. 고객사 전체를 도는 배치라 작업 토큰이 없고,
     * 이 경로는 ServiceTokenFilter가 indexer 토큰으로만 연다 (다른 고객사 경로에는 그 토큰이 통하지 않는다).
     */
    @GetMapping("/postmortems")
    List<Incident> postmortems(@PathVariable String tenantId, HttpServletRequest request,
            @RequestParam(defaultValue = "0") long afterId) {
        if (!"indexer".equals(request.getAttribute(ServiceTokenFilter.CALLER))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "회고 목록은 postmortem-indexer만 읽는다");
        }
        tenants.get(tenantId);
        return incidents.postmortems(tenantId, Math.max(0, afterId));
    }

    // ── 변경 도구 (Agent가 사람의 승인을 받은 뒤에만 호출한다) ─────────

    @PostMapping("/incidents")
    Map<String, Object> createIncident(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody CreateIncidentRequest request) {
        tenant(tenantId, taskToken);
        long id = incidents.create(tenantId, request.service(), request.title(), request.severity());
        audit.record(tenantId, "agent", "INCIDENT_CREATED", request, traceId);
        return Map.of("incidentId", id);
    }

    @PostMapping("/incidents/{incidentId}/escalate")
    Map<String, Object> escalate(@PathVariable String tenantId, @PathVariable long incidentId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody EscalateRequest request) {
        Tenant tenant = tenant(tenantId, taskToken);
        Incident incident = incidents.getForTenant(tenantId, incidentId);
        // 호출 기록을 만든다. 실제 Slack 발송은 사용자 API에서 문안·채널 검토 후 별도 승인한다.
        Map<String, Object> result = escalations.queue(tenant, incident, new EscalationService.Request(
                request.category(), request.reason(), request.service(), request.requiredAction(),
                request.blockedBy(), request.dependency()), traceId);
        if (!Boolean.TRUE.equals(result.get("duplicate"))) {
            audit.record(tenantId, "agent", "ESCALATED", Map.of("incidentId", incidentId,
                    "escalationId", result.get("escalationId"), "category", result.get("category"),
                    "reason", request.reason(), "recipients", result.get("recipients")), traceId);
        }
        return result;
    }

    @PostMapping("/actions")
    Map<String, Object> executeAction(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody ActionRequest request) {
        Tenant tenant = tenant(tenantId, taskToken);
        Incident incident = incidents.getForTenant(tenantId, request.incidentId());
        if (!tenant.allowedActions().contains(request.action())) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "'%s'는 %s 계약상 허용되지 않는다. 허용 조치: %s"
                            .formatted(request.action(), tenant.name(), tenant.allowedActions()));
        }
        String reason = "인시던트 #%d: %s".formatted(request.incidentId(),
                request.reason() == null ? "" : request.reason());
        Map<String, Object> result = changes.execute(tenant, request.action(), request.service(), request.replicas(),
                reason, incident.startedAt());
        audit.record(tenantId, "agent", ChangeService.PR_CREATED.equals(result.get("status"))
                ? "CHANGE_PR_CREATED" : "ACTION_EXECUTED", Map.of("incidentId", request.incidentId(), "result", result),
                traceId);
        return result;
    }

    @PostMapping("/notices")
    Map<String, Object> sendNotice(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody NoticeRequest request) {
        tenant(tenantId, taskToken);
        incidents.getForTenant(tenantId, request.incidentId());
        // TODO: 실제 발송(메일·메신저) 대신 감사 로그로 남기고 고객사 포털에 표시한다
        audit.record(tenantId, "agent", "NOTICE_SENT", request, traceId);
        return Map.of("sent", true);
    }

    @PostMapping("/sla-credits")
    SlaPolicy.SlaCredit confirmSlaCredit(@PathVariable String tenantId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody SlaCreditRequest request) {
        Tenant tenant = tenant(tenantId, taskToken);
        Incident incident = incidents.getForTenant(tenantId, request.incidentId());
        SlaPolicy.SlaCredit credit = sla.credit(tenant, incident.startedAt(), incident.resolvedAt());
        audit.record(tenantId, "agent", "SLA_CREDIT_CONFIRMED",
                Map.of("incidentId", incident.id(), "credit", credit), traceId);
        return credit;
    }

    /** 고객사는 작업 토큰에서 정한다. 경로의 고객사가 토큰과 다르면 막고 감사 기록을 남긴다 */
    private Tenant tenant(String tenantId, String taskToken) {
        TaskTokens.Claims claims = taskTokens.verify(taskToken);
        String caller = claims.tenantId();
        if (!tenantId.equals(caller)) {
            audit.record(caller, "agent", "TENANT_ISOLATION_BLOCKED",
                    Map.of("requested", tenantId, "taskId", claims.taskId()), null);
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "현재 작업의 고객사(%s)가 아닌 고객사(%s) 데이터는 조회할 수 없다".formatted(caller, tenantId));
        }
        return tenants.get(tenantId);
    }
}

package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class TaskService {

    public record TaskResult(String status, String answer, JsonNode sources, String traceUrl, String report) {}

    /** 고객에게 나가거나 돈이 걸린 도구는 MSP 관리자만 승인한다 (직무 분리). */
    private static final Set<String> ADMIN_ONLY_TOOLS =
            Set.of("business_send_customer_notice", "business_confirm_sla_credit");

    private static final Logger log = LoggerFactory.getLogger(TaskService.class);

    private final TaskRepository tasks;
    private final TenantRepository tenants;
    private final IncidentRepository incidents;
    private final TaskDispatcher dispatcher;
    private final AgentClient agent;
    private final TaskEventHub events;
    private final AuditLog audit;

    public TaskService(TaskRepository tasks, TenantRepository tenants, IncidentRepository incidents,
            TaskDispatcher dispatcher, AgentClient agent, TaskEventHub events, AuditLog audit) {
        this.tasks = tasks;
        this.tenants = tenants;
        this.incidents = incidents;
        this.dispatcher = dispatcher;
        this.agent = agent;
        this.events = events;
        this.audit = audit;
    }

    /** 작업을 저장하고 agent에 넘긴다. 호출한 쪽에는 taskId만 돌려준다 (202). */
    public UUID create(String tenantId, Long incidentId, String type, String question, Long requestedBy) {
        Tenant tenant = tenants.get(tenantId);
        UUID taskId = UUID.randomUUID();
        String traceId = UUID.randomUUID().toString().replace("-", "");
        tasks.create(taskId, tenantId, incidentId, type, question, requestedBy);
        try {
            dispatcher.dispatch(new TaskMessage(taskId, tenantId, tenant.slaTier(), incidentId, type, question, traceId));
        } catch (RuntimeException e) {
            log.warn("task {} dispatch failed", taskId, e);
            tasks.complete(taskId, TaskRecord.FAILED, "작업을 agent에 전달하지 못했다", null, null);
        }
        return taskId;
    }

    public void approve(UUID taskId, CurrentUser user, String decision, String comment) {
        TaskRecord task = tasks.get(taskId);
        user.requireAccess(task.tenantId());
        user.requireMspStaff();
        if (!TaskRecord.WAITING_APPROVAL.equals(task.status()) || task.pendingAction() == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task is not waiting for approval");
        }
        String tool = task.pendingAction().path("tool").asText();
        if (ADMIN_ONLY_TOOLS.contains(tool) && !user.isAdmin()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, tool + " requires MSP_ADMIN approval");
        }

        tasks.recordApproval(taskId, tool, task.pendingAction().path("args"), decision, comment, user.id());
        audit.record(task.tenantId(), user.username(), "APPROVAL_" + decision, Map.of("taskId", taskId, "tool", tool),
                null);
        tasks.resume(taskId);
        agent.resume(taskId, new AgentClient.ResumeRequest(decision, comment, user.username()));
    }

    /**
     * 반영 대기(CHANGE_PENDING)를 끝내고 agent를 재개한다.
     * decision: SYNCED(병합·동기화 완료) | TIMEOUT(기한 안에 병합되지 않음)
     */
    public boolean finishChange(TaskRecord task, String decision, String comment, String actor) {
        if (!tasks.claimChange(task.id())) {
            return false;
        }
        audit.record(task.tenantId(), actor, "CHANGE_" + decision,
                Map.of("taskId", task.id(), "change", task.pendingAction(), "comment", comment), null);
        agent.resume(task.id(), new AgentClient.ResumeRequest(decision, comment, actor));
        return true;
    }

    public void onEvent(UUID taskId, String type, JsonNode data) {
        if (!TaskEventHub.EVENT_TYPES.contains(type)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unknown event type " + type);
        }
        tasks.markRunningIfQueued(taskId);
        if ("APPROVAL_REQUIRED".equals(type)) {
            tasks.waitForApproval(taskId, data);
        } else if ("CHANGE_PENDING".equals(type)) {
            tasks.waitForChange(taskId, data);
        }
        events.publish(taskId, type, data);
    }

    /** agent는 DB에 쓰지 않는다. 결과와 보고서는 backend가 저장한다. */
    public void complete(UUID taskId, TaskResult result) {
        TaskRecord task = tasks.get(taskId);
        String status = TaskRecord.DONE.equals(result.status()) ? TaskRecord.DONE : TaskRecord.FAILED;
        tasks.complete(taskId, status, result.answer(), result.sources(), result.traceUrl());
        if (task.incidentId() != null && result.report() != null) {
            incidents.saveReport(task.incidentId(), result.report());
        }
        events.publish(taskId, status, Map.of("taskId", taskId, "status", status));
    }
}

package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.settings.SettingsService;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

@Service
public class TaskService {

    public record TaskResult(String status, String answer, JsonNode sources, JsonNode summary, String traceUrl,
            String report) {}

    /** 고객에게 나가거나 돈이 걸린 도구는 MSP 관리자만 승인한다 (직무 분리). */
    private static final Set<String> ADMIN_ONLY_TOOLS =
            Set.of("business_send_customer_notice", "business_confirm_sla_credit");

    private static final Logger log = LoggerFactory.getLogger(TaskService.class);

    private static final String AUTO_APPROVER = "auto-approve";

    private final TaskRepository tasks;
    private final TenantRepository tenants;
    private final IncidentRepository incidents;
    private final TaskDispatcher dispatcher;
    private final AgentClient agent;
    private final TaskEventHub events;
    private final AuditLog audit;
    private final SettingsService settings;
    private final TransactionTemplate transactions;

    public TaskService(TaskRepository tasks, TenantRepository tenants, IncidentRepository incidents,
            TaskDispatcher dispatcher, AgentClient agent, TaskEventHub events, AuditLog audit,
            SettingsService settings, PlatformTransactionManager transactionManager) {
        this.tasks = tasks;
        this.tenants = tenants;
        this.incidents = incidents;
        this.dispatcher = dispatcher;
        this.agent = agent;
        this.events = events;
        this.audit = audit;
        this.settings = settings;
        this.transactions = new TransactionTemplate(transactionManager);
    }

    /** 작업을 저장하고 agent에 넘긴다. 호출한 쪽에는 taskId만 돌려준다 (202). */
    public UUID create(String tenantId, Long incidentId, String type, String question, Long requestedBy) {
        Tenant tenant = tenants.get(tenantId);
        if (incidentId != null) {
            incidents.getForTenant(tenantId, incidentId);
        }
        UUID taskId = UUID.randomUUID();
        String traceId = UUID.randomUUID().toString().replace("-", "");
        tasks.create(taskId, tenantId, incidentId, type, question, requestedBy);
        try {
            dispatcher.dispatch(new TaskMessage(taskId, tenantId, tenant.slaTier(), incidentId, type, question, traceId));
        } catch (RuntimeException e) {
            log.warn("task {} dispatch failed", taskId, e);
            tasks.complete(taskId, TaskRecord.FAILED, "작업을 agent에 전달하지 못했다", null, null, null);
        }
        return taskId;
    }

    public void approve(UUID taskId, CurrentUser user, String decision, String comment, String approvalId) {
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
        String expectedId = task.pendingAction().path("approvalId").asText(null);
        if (expectedId != null && !expectedId.equals(approvalId)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "승인 요청이 변경되었다. 화면을 새로고침한다");
        }
        decide(task, tool, decision, comment, user.username(), user.id());
    }

    /** 사람 대신 바로 승인한다. approvals 테이블은 사람(users)만 기록하므로 감사 로그와 진행 이벤트에만 남긴다. */
    private void autoApprove(UUID taskId, JsonNode pendingAction) {
        try {
            decide(tasks.get(taskId), pendingAction.path("tool").asText(), "APPROVE", "자동 승인 설정으로 승인",
                    AUTO_APPROVER, null);
        } catch (ResponseStatusException e) {
            // 자동 재개 전달 실패여도 복원된 승인 카드를 사람이 다시 처리할 수 있다.
            log.warn("task {} auto approval was not delivered: {}", taskId, e.getReason());
        }
    }

    private void decide(TaskRecord task, String tool, String decision, String comment, String actor, Long userId) {
        Map<String, String> decided = Map.of("tool", tool, "decision", decision, "by", actor);
        Boolean claimed = transactions.execute(tx -> {
            if (!tasks.claimApproval(task.id(), task.pendingAction())) {
                return false;
            }
            if (userId != null) {
                tasks.recordApproval(task.id(), tool, task.pendingAction().path("args"), decision, comment, userId);
            }
            audit.record(task.tenantId(), actor, "APPROVAL_" + decision, Map.of("taskId", task.id(), "tool", tool), null);
            tasks.addEvent(task.id(), "APPROVAL_DECIDED", decided);
            return true;
        });
        if (!Boolean.TRUE.equals(claimed)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "이미 처리된 승인 요청이다");
        }
        events.publish(task.id(), "APPROVAL_DECIDED", decided);
        try {
            agent.resume(task.id(), new AgentClient.ResumeRequest(decision, comment, actor,
                    task.pendingAction().path("approvalId").asText(null)));
            tasks.clearApproval(task.id(), task.pendingAction());
        } catch (RuntimeException e) {
            if (tasks.restoreApproval(task.id(), task.pendingAction())) {
                tasks.addEvent(task.id(), "APPROVAL_REQUIRED", task.pendingAction());
                events.publish(task.id(), "APPROVAL_REQUIRED", task.pendingAction());
            }
            audit.record(task.tenantId(), actor, "APPROVAL_DELIVERY_FAILED", Map.of("taskId", task.id()), null);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "Agent에 승인 결정을 전달하지 못했다. 같은 승인 요청을 다시 시도할 수 있다", e);
        }
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
        tasks.addEvent(task.id(), "CHANGE_FINISHED", Map.of("result", decision));
        events.publish(task.id(), "CHANGE_FINISHED", Map.of("result", decision));
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
        if (!"TOKEN".equals(type)) {
            tasks.addEvent(taskId, type, data);
        }
        events.publish(taskId, type, data);
        if ("APPROVAL_REQUIRED".equals(type) && settings.autoApprove()) {
            autoApprove(taskId, data);
        }
    }

    /** agent는 DB에 쓰지 않는다. 결과와 보고서는 backend가 저장한다. */
    public void complete(UUID taskId, TaskResult result) {
        TaskRecord task = tasks.get(taskId);
        if (task.incidentId() != null && result.report() != null) {
            incidents.getForTenant(task.tenantId(), task.incidentId());
        }
        String status = TaskRecord.DONE.equals(result.status()) ? TaskRecord.DONE : TaskRecord.FAILED;
        tasks.complete(taskId, status, result.answer(), result.sources(), result.summary(), result.traceUrl());
        if (task.incidentId() != null && result.report() != null) {
            incidents.saveReport(task.tenantId(), task.incidentId(), result.report());
        }
        tasks.addEvent(taskId, status, Map.of());
        events.publish(taskId, status, Map.of("taskId", taskId, "status", status));
    }
}

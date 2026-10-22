package com.opslens.backend.task;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.settings.SettingsService;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.web.server.ResponseStatusException;

class TaskServiceTest {
    private final TaskRepository tasks = mock(TaskRepository.class);
    private final TenantRepository tenants = mock(TenantRepository.class);
    private final IncidentRepository incidents = mock(IncidentRepository.class);
    private final TaskOutboxRepository outbox = mock(TaskOutboxRepository.class);
    private final TaskOutboxRelay relay = mock(TaskOutboxRelay.class);
    private final AgentClient agent = mock(AgentClient.class);
    private final TaskEventHub events = mock(TaskEventHub.class);
    private final AuditLog audit = mock(AuditLog.class);
    private final SettingsService settings = mock(SettingsService.class);
    private final PlatformTransactionManager transactions = mock(PlatformTransactionManager.class);
    private final UUID taskId = UUID.randomUUID();
    private final CurrentUser engineer = new CurrentUser(2L, "engineer", CurrentUser.MSP_ENGINEER, List.of("tenant-a"));
    private final CurrentUser admin = new CurrentUser(1L, "admin", CurrentUser.MSP_ADMIN, List.of("tenant-a"));
    private final ObjectMapper json = new ObjectMapper();
    private TaskService service;
    private JsonNode pending;

    @BeforeEach
    void setup() throws Exception {
        when(transactions.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        pending = json.readTree("""
                {"tool":"business_execute_runbook_action","approvalId":"current",
                 "args":{"incident_id":1,"action":"restart_pods","service":"order-api"}}
                """);
        when(tasks.get(taskId)).thenReturn(task(pending));
        service = new TaskService(tasks, tenants, incidents, outbox, relay, agent, events, audit, settings, transactions, mock(com.opslens.backend.attachment.TaskAttachments.class));
    }

    private TaskRecord task(JsonNode action) {
        return new TaskRecord(taskId, "tenant-a", 1L, "DIAGNOSE", "test", TaskRecord.WAITING_APPROVAL,
                null, action, null, null, null, null, OffsetDateTime.now(), OffsetDateTime.now());
    }

    @Test
    void foreignIncidentIsRejectedBeforeSavingOrDispatching() {
        when(tenants.get("tenant-a")).thenReturn(new Tenant("tenant-a", "A", "GOLD", BigDecimal.TEN,
                15, List.of(), "DIRECT", "tenant-a", null));
        when(incidents.getForTenant("tenant-a", 99L)).thenThrow(new ResponseStatusException(HttpStatus.NOT_FOUND));
        assertThrows(ResponseStatusException.class, () -> service.create("tenant-a", 99L, "QUESTION", "test", 2L));
        verify(tasks, never()).create(any(), anyString(), any(), anyString(), anyString(), any());
        verifyNoInteractions(outbox, relay);
    }

    @Test
    void createSavesTaskAndOutboxTogetherThenKicksRelayAfterCommit() {
        when(tenants.get("tenant-a")).thenReturn(new Tenant("tenant-a", "A", "GOLD", BigDecimal.TEN,
                15, List.of(), "DIRECT", "tenant-a", null));
        UUID created = service.create("tenant-a", null, "QUESTION", "test", 2L);
        var order = inOrder(transactions, tasks, outbox, relay);
        order.verify(transactions).getTransaction(any());
        order.verify(tasks).create(eq(created), eq("tenant-a"), isNull(), eq("QUESTION"), eq("test"), eq(2L));
        order.verify(outbox).enqueue(argThat(m -> m.taskId().equals(created) && m.slaTier().equals("GOLD")));
        order.verify(transactions).commit(any());
        order.verify(relay).kick(created);
    }

    @Test
    void outboxFailureRollsBackAndDoesNotKick() {
        when(tenants.get("tenant-a")).thenReturn(new Tenant("tenant-a", "A", "GOLD", BigDecimal.TEN,
                15, List.of(), "DIRECT", "tenant-a", null));
        doThrow(new IllegalStateException("db")).when(outbox).enqueue(any());
        assertThrows(IllegalStateException.class, () -> service.create("tenant-a", null, "QUESTION", "test", 2L));
        verify(transactions).rollback(any());
        verifyNoInteractions(relay);
    }

    @Test
    void foreignReportIsRejectedBeforeCompletingTask() {
        when(incidents.getForTenant("tenant-a", 1L)).thenThrow(new ResponseStatusException(HttpStatus.NOT_FOUND));
        assertThrows(ResponseStatusException.class,
                () -> service.complete(taskId, new TaskService.TaskResult("DONE", "report", null, null, null, "report")));
        verify(tasks, never()).complete(any(), any(), any(), any(), any(), any());
        verify(incidents, never()).saveReport(anyString(), anyLong(), anyString());
    }

    @Test
    void duplicateApprovalDoesNotRecordOrResume() {
        when(tasks.claimApproval(taskId, pending)).thenReturn(false);
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> service.approve(taskId, engineer, "APPROVE", null, "current"));
        assertEquals(HttpStatus.CONFLICT, error.getStatusCode());
        verify(tasks, never()).recordApproval(any(), any(), any(), any(), any(), anyLong());
        verifyNoInteractions(agent);
    }

    @Test
    void staleApprovalIdIsRejectedBeforeClaim() {
        assertThrows(ResponseStatusException.class,
                () -> service.approve(taskId, engineer, "APPROVE", null, "old"));
        verify(tasks, never()).claimApproval(any(), any());
        verifyNoInteractions(agent);
    }

    @Test
    void successfulApprovalCommitsBeforeResumeAndKeepsIdentity() {
        when(tasks.claimApproval(taskId, pending)).thenReturn(true);
        service.approve(taskId, engineer, "APPROVE", "test", "current");
        var order = inOrder(transactions, agent);
        order.verify(transactions).commit(any());
        order.verify(agent).resume(taskId, new AgentClient.ResumeRequest("APPROVE", "test", "engineer", "current"));
        verify(tasks).clearApproval(taskId, pending);
    }

    @Test
    void failedResumeRestoresPendingApprovalForRetry() {
        when(tasks.claimApproval(taskId, pending)).thenReturn(true);
        when(tasks.restoreApproval(taskId, pending)).thenReturn(true);
        doThrow(new IllegalStateException("offline")).when(agent).resume(any(), any());
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> service.approve(taskId, engineer, "APPROVE", null, "current"));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.getStatusCode());
        verify(tasks).restoreApproval(taskId, pending);
        verify(tasks, never()).clearApproval(any(), any());
        verify(events).publish(taskId, "APPROVAL_REQUIRED", pending);
    }

    @Test
    void adminOnlyNoticeCannotBeApprovedByEngineer() throws Exception {
        JsonNode notice = json.readTree("""
                {"tool":"business_send_customer_notice","approvalId":"current","args":{"incident_id":1}}
                """);
        when(tasks.get(taskId)).thenReturn(task(notice));
        ResponseStatusException error = assertThrows(ResponseStatusException.class,
                () -> service.approve(taskId, engineer, "APPROVE", null, "current"));
        assertEquals(HttpStatus.FORBIDDEN, error.getStatusCode());
        verify(tasks, never()).claimApproval(any(), any());
        when(tasks.claimApproval(taskId, notice)).thenReturn(true);
        service.approve(taskId, admin, "APPROVE", null, "current");
        verify(agent).resume(taskId, new AgentClient.ResumeRequest("APPROVE", null, "admin", "current"));
    }

    @Test
    void testAutoApprovalAlsoUsesAtomicClaimAndIdentity() {
        when(settings.autoApprove()).thenReturn(true);
        when(tasks.claimApproval(taskId, pending)).thenReturn(true);
        service.onEvent(taskId, "APPROVAL_REQUIRED", pending);
        verify(tasks).claimApproval(taskId, pending);
        verify(agent).resume(taskId, new AgentClient.ResumeRequest("APPROVE", "자동 승인 설정으로 승인", "auto-approve", "current"));
        verify(tasks, never()).recordApproval(any(), any(), any(), any(), any(), anyLong());
    }
}

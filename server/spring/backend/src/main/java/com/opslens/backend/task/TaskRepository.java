package com.opslens.backend.task;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

@Repository
public class TaskRepository {

    /** 작업 행과, 그 작업을 다시 실행한 새 작업 ID */
    private static final String SELECT_TASK =
            "SELECT *, (SELECT r.id FROM ai_tasks r WHERE r.retry_of = ai_tasks.id) AS retried_by FROM ai_tasks ";

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public TaskRepository(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public void create(UUID id, String tenantId, Long incidentId, String type, String question, Long requestedBy) {
        create(id, tenantId, incidentId, type, question, requestedBy, null);
    }

    /** retryOf: 다시 실행한 원래 작업. 같은 작업을 두 번 다시 실행하면 유일 인덱스(ai_tasks_retry_of)에 걸린다 */
    public void create(UUID id, String tenantId, Long incidentId, String type, String question, Long requestedBy,
            UUID retryOf) {
        jdbc.sql("""
                        INSERT INTO ai_tasks (id, tenant_id, incident_id, type, question, status, requested_by, retry_of)
                        VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?)
                        """)
                .params(id, tenantId, incidentId, type, question, requestedBy, retryOf)
                .update();
    }

    public TaskRecord get(UUID id) {
        return jdbc.sql(SELECT_TASK + "WHERE id = ?").param(id).query(this::toTask).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown task " + id));
    }

    public List<TaskRecord> listByRequester(long userId) {
        return jdbc.sql(SELECT_TASK + "WHERE requested_by = ? ORDER BY created_at DESC LIMIT 50")
                .param(userId).query(this::toTask).list();
    }

    public List<TaskRecord> listByTenants(List<String> tenantIds) {
        return jdbc.sql(SELECT_TASK + "WHERE tenant_id = ANY(?) ORDER BY created_at DESC LIMIT 50")
                .param(tenantIds.toArray(String[]::new)).query(this::toTask).list();
    }

    /** QUEUED였던 작업만 RUNNING으로 바꾼다 (첫 진행 이벤트가 도착했을 때). */
    public void markRunningIfQueued(UUID id) {
        jdbc.sql("UPDATE ai_tasks SET status = 'RUNNING', updated_at = now() WHERE id = ? AND status = 'QUEUED'")
                .param(id).update();
    }

    /** 끝난 작업(DONE·FAILED)은 바꾸지 않는다. 임대를 잃은 파드가 뒤늦게 보낸 이벤트가 끝난 작업을 되살리지 않게 한다. */
    public boolean waitForApproval(UUID id, JsonNode pendingAction) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'WAITING_APPROVAL', pending_action = ?::jsonb, updated_at = now()
                        WHERE id = ? AND status NOT IN ('DONE', 'FAILED')
                        """)
                .params(toJson(pendingAction), id).update() == 1;
    }

    public boolean waitForChange(UUID id, JsonNode change) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'CHANGE_PENDING', pending_action = ?::jsonb, updated_at = now()
                        WHERE id = ? AND status NOT IN ('DONE', 'FAILED')
                        """)
                .params(toJson(change), id).update() == 1;
    }

    public List<TaskRecord> listChangePending(String tenantId) {
        return jdbc.sql(SELECT_TASK + "WHERE status = 'CHANGE_PENDING' AND tenant_id = ?")
                .param(tenantId).query(this::toTask).list();
    }

    public List<TaskRecord> listChangePendingSince(OffsetDateTime before) {
        return jdbc.sql(SELECT_TASK + "WHERE status = 'CHANGE_PENDING' AND updated_at < ?")
                .param(before).query(this::toTask).list();
    }

    /** 반영 대기를 끝낸다. 동기화 알림과 시간 초과가 겹쳐도 한 번만 재개되도록 상태를 조건으로 건다. */
    public boolean claimChange(UUID id) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'RUNNING', pending_action = NULL, updated_at = now()
                        WHERE id = ? AND status = 'CHANGE_PENDING'
                        """)
                .param(id).update() == 1;
    }

    /**
     * 재개 전달에 실패한 반영 대기를 되돌린다. updated_at은 처음 기다리기 시작한 시각으로 두어
     * 병합 기한(merge-timeout) 계산이 늦춰지지 않게 한다.
     */
    public boolean restoreChange(UUID id, JsonNode change, OffsetDateTime waitingSince) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'CHANGE_PENDING', pending_action = ?::jsonb, updated_at = ?
                        WHERE id = ? AND status = 'RUNNING' AND pending_action IS NULL
                        """)
                .params(toJson(change), waitingSince, id).update() == 1;
    }

    /** 승인 대상을 비교해 동시에 온 승인 중 하나만 차지한다. 전달 실패 시 복원할 수 있게 인자를 남긴다. */
    public boolean claimApproval(UUID id, JsonNode expected) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'RUNNING', updated_at = now()
                        WHERE id = ? AND status = 'WAITING_APPROVAL' AND pending_action = ?::jsonb
                        """)
                .params(id, toJson(expected)).update() == 1;
    }

    public void clearApproval(UUID id, JsonNode expected) {
        jdbc.sql("""
                        UPDATE ai_tasks SET pending_action = NULL
                        WHERE id = ? AND status = 'RUNNING' AND pending_action = ?::jsonb
                        """).params(id, toJson(expected)).update();
    }

    public boolean restoreApproval(UUID id, JsonNode expected) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = 'WAITING_APPROVAL', updated_at = now()
                        WHERE id = ? AND status = 'RUNNING' AND pending_action = ?::jsonb
                        """).params(id, toJson(expected)).update() == 1;
    }

    /** 처음 끝난 결과만 남긴다. 중복 결과나 늦게 온 결과(예: 전달 실패로 FAILED 처리한 뒤 도착한 DONE)는 무시한다. */
    public boolean complete(UUID id, String status, String answer, JsonNode sources, JsonNode summary,
            String traceUrl) {
        return jdbc.sql("""
                        UPDATE ai_tasks SET status = ?, answer = ?, sources = ?::jsonb, summary = ?::jsonb,
                               trace_url = ?, pending_action = NULL, updated_at = now()
                        WHERE id = ? AND status NOT IN ('DONE', 'FAILED')
                        """)
                .params(status, answer, toJson(sources), toJson(summary), traceUrl, id).update() == 1;
    }

    public void addEvent(UUID taskId, String type, Object data) {
        jdbc.sql("INSERT INTO task_events (task_id, type, data) VALUES (?, ?, ?::jsonb)")
                .params(taskId, type, toJson(json.valueToTree(data))).update();
    }

    public List<TaskEvent> listEvents(UUID taskId) {
        return jdbc.sql("SELECT type, data, created_at FROM task_events WHERE task_id = ? ORDER BY id")
                .param(taskId)
                .query((rs, rowNum) -> new TaskEvent(rs.getString("type"), readJson(rs.getString("data")),
                        rs.getObject("created_at", OffsetDateTime.class)))
                .list();
    }

    public void recordApproval(UUID taskId, String toolName, JsonNode args, String decision, String comment,
            long decidedBy) {
        jdbc.sql("""
                        INSERT INTO approvals (task_id, tool_name, args, decision, comment, decided_by)
                        VALUES (?, ?, ?::jsonb, ?, ?, ?)
                        """)
                .params(taskId, toolName, toJson(args), decision, comment, decidedBy).update();
    }

    private TaskRecord toTask(ResultSet rs, int rowNum) throws SQLException {
        return new TaskRecord(rs.getObject("id", UUID.class), rs.getString("tenant_id"),
                rs.getObject("incident_id", Long.class), rs.getString("type"), rs.getString("question"),
                rs.getString("status"), rs.getObject("requested_by", Long.class),
                readJson(rs.getString("pending_action")), rs.getString("answer"),
                readJson(rs.getString("sources")), readJson(rs.getString("summary")), rs.getString("trace_url"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class),
                rs.getObject("retry_of", UUID.class), rs.getObject("retried_by", UUID.class));
    }

    private JsonNode readJson(String value) {
        try {
            return value == null ? null : json.readTree(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private String toJson(JsonNode node) {
        return node == null || node.isNull() ? null : node.toString();
    }
}

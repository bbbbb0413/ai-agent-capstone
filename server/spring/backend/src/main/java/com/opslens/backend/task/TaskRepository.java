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

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public TaskRepository(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public void create(UUID id, String tenantId, Long incidentId, String type, String question, Long requestedBy) {
        jdbc.sql("""
                        INSERT INTO ai_tasks (id, tenant_id, incident_id, type, question, status, requested_by)
                        VALUES (?, ?, ?, ?, ?, 'QUEUED', ?)
                        """)
                .params(id, tenantId, incidentId, type, question, requestedBy)
                .update();
    }

    public TaskRecord get(UUID id) {
        return jdbc.sql("SELECT * FROM ai_tasks WHERE id = ?").param(id).query(this::toTask).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown task " + id));
    }

    public List<TaskRecord> listByRequester(long userId) {
        return jdbc.sql("SELECT * FROM ai_tasks WHERE requested_by = ? ORDER BY created_at DESC LIMIT 50")
                .param(userId).query(this::toTask).list();
    }

    public List<TaskRecord> listByTenants(List<String> tenantIds) {
        return jdbc.sql("SELECT * FROM ai_tasks WHERE tenant_id = ANY(?) ORDER BY created_at DESC LIMIT 50")
                .param(tenantIds.toArray(String[]::new)).query(this::toTask).list();
    }

    /** QUEUED였던 작업만 RUNNING으로 바꾼다 (첫 진행 이벤트가 도착했을 때). */
    public void markRunningIfQueued(UUID id) {
        jdbc.sql("UPDATE ai_tasks SET status = 'RUNNING', updated_at = now() WHERE id = ? AND status = 'QUEUED'")
                .param(id).update();
    }

    public void waitForApproval(UUID id, JsonNode pendingAction) {
        jdbc.sql("""
                        UPDATE ai_tasks SET status = 'WAITING_APPROVAL', pending_action = ?::jsonb, updated_at = now()
                        WHERE id = ?
                        """)
                .params(toJson(pendingAction), id).update();
    }

    public void resume(UUID id) {
        jdbc.sql("UPDATE ai_tasks SET status = 'RUNNING', pending_action = NULL, updated_at = now() WHERE id = ?")
                .param(id).update();
    }

    public void complete(UUID id, String status, String answer, JsonNode sources, String traceUrl) {
        jdbc.sql("""
                        UPDATE ai_tasks SET status = ?, answer = ?, sources = ?::jsonb, trace_url = ?,
                               pending_action = NULL, updated_at = now()
                        WHERE id = ?
                        """)
                .params(status, answer, toJson(sources), traceUrl, id).update();
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
                readJson(rs.getString("sources")), rs.getString("trace_url"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));
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

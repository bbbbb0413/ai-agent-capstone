package com.opslens.backend.escalation;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class EscalationRepository {

    private static final TypeReference<List<Escalation.Recipient>> RECIPIENTS = new TypeReference<>() {};

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public EscalationRepository(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public long insert(String tenantId, long incidentId, EscalationService.Request request,
            List<Escalation.Recipient> recipients, String message, String traceId) {
        return jdbc.sql("""
                        INSERT INTO escalations (tenant_id, incident_id, category, reason, service, required_action,
                                                 blocked_by, dependency, recipients, message, trace_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?) RETURNING id
                        """)
                .params(tenantId, incidentId, request.category(), request.reason(), request.service(),
                        request.requiredAction(), request.blockedBy(), request.dependency(), toJson(recipients),
                        message, traceId)
                .query(Long.class).single();
    }

    /** 같은 인시던트의 같은 요청이 아직 발송 대기 중이면 그 건. Agent가 재시도해도 중복으로 쌓지 않는다. */
    public Optional<Escalation> findQueued(long incidentId, EscalationService.Request request) {
        return jdbc.sql("""
                        SELECT * FROM escalations
                        WHERE incident_id = ? AND status = 'QUEUED' AND category = ?
                          AND required_action IS NOT DISTINCT FROM ? AND dependency IS NOT DISTINCT FROM ?
                        ORDER BY id LIMIT 1
                        """)
                .params(incidentId, request.category(), request.requiredAction(), request.dependency())
                .query(this::toEscalation).optional();
    }

    public List<Escalation> listForIncident(String tenantId, long incidentId) {
        return jdbc.sql("SELECT * FROM escalations WHERE tenant_id = ? AND incident_id = ? ORDER BY id")
                .params(tenantId, incidentId)
                .query(this::toEscalation).list();
    }

    private Escalation toEscalation(ResultSet rs, int rowNum) throws SQLException {
        return new Escalation(rs.getLong("id"), rs.getString("tenant_id"), rs.getLong("incident_id"),
                rs.getString("category"), rs.getString("reason"), rs.getString("service"),
                rs.getString("required_action"), rs.getString("blocked_by"), rs.getString("dependency"),
                fromJson(rs.getString("recipients")), rs.getString("message"), rs.getString("status"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("sent_at", OffsetDateTime.class));
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }

    private List<Escalation.Recipient> fromJson(String value) {
        try {
            return json.readValue(value, RECIPIENTS);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }
}

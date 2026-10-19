package com.opslens.backend.audit;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** 누가 어느 고객사에 무엇을 했는지 남긴다. 고객사별 감사 로그와 격리 위반 기록에 쓴다. */
@Component
public class AuditLog {

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public AuditLog(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public void record(String tenantId, String actor, String action, Object detail, String traceId) {
        jdbc.sql("INSERT INTO audit_logs (tenant_id, actor, action, detail, trace_id) VALUES (?, ?, ?, ?::jsonb, ?)")
                .params(tenantId, actor, action, toJson(detail), traceId)
                .update();
    }

    private String toJson(Object detail) {
        try {
            return json.writeValueAsString(detail);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }
}

package com.opslens.backend.incident;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

@Repository
public class IncidentRepository {

    private final JdbcClient jdbc;

    public IncidentRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 같은 알림(fingerprint)으로 열린 인시던트가 없을 때만 만든다. 이미 있으면 빈 값을 돌려준다. */
    public Optional<Long> openIfAbsent(String tenantId, String service, String title, String severity,
            String fingerprint) {
        return jdbc.sql("""
                        INSERT INTO incidents (tenant_id, service, title, severity, fingerprint)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (fingerprint) WHERE status = 'OPEN' DO NOTHING
                        RETURNING id
                        """)
                .params(tenantId, service, title, severity, fingerprint)
                .query(Long.class).optional();
    }

    public long create(String tenantId, String service, String title, String severity) {
        return jdbc.sql("INSERT INTO incidents (tenant_id, service, title, severity) VALUES (?, ?, ?, ?) RETURNING id")
                .params(tenantId, service, title, severity)
                .query(Long.class).single();
    }

    public void resolveByFingerprint(String fingerprint) {
        jdbc.sql("UPDATE incidents SET status = 'RESOLVED', resolved_at = now() WHERE fingerprint = ? AND status = 'OPEN'")
                .param(fingerprint).update();
    }

    /** 사람이 복구를 확인한 인시던트를 닫는다. 알림 fingerprint가 없는 제보 인시던트도 이 경로로 종료할 수 있다. */
    public boolean resolveManually(String tenantId, long id) {
        return jdbc.sql("""
                        UPDATE incidents SET status = 'RESOLVED', resolved_at = now()
                        WHERE id = ? AND tenant_id = ? AND status != 'RESOLVED'
                        """)
                .params(id, tenantId).update() == 1;
    }

    public void saveReport(String tenantId, long id, String report) {
        int updated = jdbc.sql("UPDATE incidents SET report = ? WHERE id = ? AND tenant_id = ?")
                .params(report, id, tenantId).update();
        if (updated != 1) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown incident " + id);
        }
    }

    public Incident get(long id) {
        return jdbc.sql("SELECT * FROM incidents WHERE id = ?").param(id)
                .query(IncidentRepository::toIncident).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown incident " + id));
    }

    /** 다른 고객사의 인시던트 번호를 넘겨도 찾지 못하게 tenant 조건을 함께 건다. */
    public Incident getForTenant(String tenantId, long id) {
        Incident incident = get(id);
        if (!incident.tenantId().equals(tenantId)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown incident " + id);
        }
        return incident;
    }

    public List<Incident> list(List<String> tenantIds, String status) {
        return jdbc.sql("""
                        SELECT * FROM incidents
                        WHERE tenant_id = ANY(?) AND (CAST(? AS TEXT) IS NULL OR status = ?)
                        ORDER BY started_at DESC LIMIT 100
                        """)
                .params(tenantIds.toArray(String[]::new), status, status)
                .query(IncidentRepository::toIncident).list();
    }

    public List<Notice> notices(String tenantId, long incidentId) {
        return jdbc.sql("""
                        SELECT detail ->> 'message' AS message, created_at FROM audit_logs
                        WHERE tenant_id = ? AND action = 'NOTICE_SENT' AND (detail ->> 'incidentId')::bigint = ?
                        ORDER BY created_at
                        """)
                .params(tenantId, incidentId)
                .query((rs, rowNum) -> new Notice(rs.getString("message"),
                        rs.getObject("created_at", OffsetDateTime.class)))
                .list();
    }

    /** 색인기가 전체 이력을 순회한다. OPEN·빈 보고서·실패한 작업 결과는 회고로 보내지 않는다. */
    public List<Incident> postmortems(String tenantId, long afterId) {
        return jdbc.sql("""
                        SELECT i.* FROM incidents i
                        WHERE i.tenant_id = ? AND i.id > ? AND i.status = 'RESOLVED'
                          AND i.resolved_at IS NOT NULL AND length(trim(i.report)) > 0
                          AND EXISTS (SELECT 1 FROM ai_tasks t
                                      WHERE t.incident_id = i.id AND t.tenant_id = i.tenant_id
                                        AND t.status = 'DONE' AND t.answer = i.report)
                        ORDER BY i.id LIMIT 100
                        """)
                .params(tenantId, afterId).query(IncidentRepository::toIncident).list();
    }

    private static Incident toIncident(ResultSet rs, int rowNum) throws SQLException {
        return new Incident(rs.getLong("id"), rs.getString("tenant_id"), rs.getString("service"),
                rs.getString("title"), rs.getString("severity"), rs.getString("status"), rs.getString("report"),
                rs.getObject("started_at", OffsetDateTime.class), rs.getObject("resolved_at", OffsetDateTime.class));
    }
}

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

    public void saveReport(long id, String report) {
        jdbc.sql("UPDATE incidents SET report = ? WHERE id = ?").params(report, id).update();
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

    private static Incident toIncident(ResultSet rs, int rowNum) throws SQLException {
        return new Incident(rs.getLong("id"), rs.getString("tenant_id"), rs.getString("service"),
                rs.getString("title"), rs.getString("severity"), rs.getString("status"), rs.getString("report"),
                rs.getObject("started_at", OffsetDateTime.class), rs.getObject("resolved_at", OffsetDateTime.class));
    }
}

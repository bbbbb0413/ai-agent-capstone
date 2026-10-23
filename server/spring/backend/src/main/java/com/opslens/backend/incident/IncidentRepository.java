package com.opslens.backend.incident;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Repository
public class IncidentRepository {

    private final JdbcClient jdbc;

    public IncidentRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 알림을 받은 결과. opened면 새 인시던트, attached면 열린 인시던트에 덧붙였다. 둘 다 아니면 이미 받은 알림이다. */
    public record AlertOutcome(long incidentId, boolean opened, boolean attached) {}

    /**
     * 같은 (고객사, 서비스)에 열린 인시던트가 있으면 알림을 덧붙이고, 없으면 인시던트를 연다 (SPEC-compound-faults 5장).
     * 원인이 겹쳐 HighErrorRate와 HighLatency가 함께 울려도 인시던트와 작업은 하나다. 덧붙인 알림의 심각도가 더 높으면 올린다.
     * 알림이 모두 해제돼 닫혔어도 그 인시던트의 진단 작업이 아직 진행 중이면 다시 열고 덧붙인다. 파드 멈춤을 재시작으로
     * 고친 직후 에러율 알림이 풀리고 가려져 있던 결제 지연 알림이 울리면, 작업 하나가 두 원인을 이어서 처리해야 한다.
     * 서비스 라벨이 없는 알림은 묶을 기준이 없어 알림마다 인시던트를 연다.
     * 두 알림이 동시에 와도 인시던트가 둘 생기지 않게 (고객사, 서비스)마다 잠근다.
     */
    @Transactional
    public AlertOutcome openOrAttach(String tenantId, String service, String alertname, String title, String severity,
            String fingerprint) {
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))")
                .param("incident-alerts:" + tenantId + "/" + (service == null ? fingerprint : service)).query().listOfRows();
        Optional<Long> firing = jdbc.sql("SELECT incident_id FROM incident_alerts WHERE fingerprint = ? AND resolved_at IS NULL")
                .param(fingerprint).query(Long.class).optional();
        if (firing.isPresent()) {
            return new AlertOutcome(firing.get(), false, false);
        }
        Optional<Long> open = service == null ? Optional.empty() : jdbc.sql("""
                        SELECT id FROM incidents i WHERE tenant_id = ? AND service = ?
                          AND (status <> 'RESOLVED' OR EXISTS (
                                SELECT 1 FROM ai_tasks t WHERE t.incident_id = i.id AND t.type = 'DIAGNOSE'
                                  AND t.status NOT IN ('DONE', 'FAILED')))
                        ORDER BY id DESC LIMIT 1
                        """).params(tenantId, service).query(Long.class).optional();
        open.ifPresent(id -> jdbc.sql("""
                        UPDATE incidents SET status = 'OPEN', resolved_at = NULL WHERE id = ? AND status = 'RESOLVED'
                        """).param(id).update());
        long incidentId = open.orElseGet(() -> jdbc.sql("""
                        INSERT INTO incidents (tenant_id, service, title, severity, fingerprint)
                        VALUES (?, ?, ?, ?, ?) RETURNING id
                        """).params(tenantId, service, title, severity, fingerprint).query(Long.class).single());
        jdbc.sql("""
                        INSERT INTO incident_alerts (incident_id, fingerprint, alertname, title, severity)
                        VALUES (?, ?, ?, ?, ?)
                        """).params(incidentId, fingerprint, alertname, title, severity).update();
        if (open.isPresent()) {
            // P1 < P2 < P3 순이라 문자열로 작은 쪽이 더 심각하다
            jdbc.sql("UPDATE incidents SET severity = ? WHERE id = ? AND severity > ?")
                    .params(severity, incidentId, severity).update();
        }
        return new AlertOutcome(incidentId, open.isEmpty(), open.isPresent());
    }

    public long create(String tenantId, String service, String title, String severity) {
        return jdbc.sql("INSERT INTO incidents (tenant_id, service, title, severity) VALUES (?, ?, ?, ?) RETURNING id")
                .params(tenantId, service, title, severity)
                .query(Long.class).single();
    }

    /** 알림이 해제됐다. 그 인시던트에 묶인 알림이 모두 해제됐을 때만 인시던트를 닫는다. */
    @Transactional
    public void resolveByFingerprint(String fingerprint) {
        List<Long> touched = jdbc.sql("""
                        UPDATE incident_alerts SET resolved_at = now() WHERE fingerprint = ? AND resolved_at IS NULL
                        RETURNING incident_id
                        """).param(fingerprint).query(Long.class).list();
        for (long incidentId : touched) {
            jdbc.sql("""
                            UPDATE incidents SET status = 'RESOLVED', resolved_at = now()
                            WHERE id = ? AND status = 'OPEN'
                              AND NOT EXISTS (SELECT 1 FROM incident_alerts WHERE incident_id = ? AND resolved_at IS NULL)
                            """).params(incidentId, incidentId).update();
        }
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

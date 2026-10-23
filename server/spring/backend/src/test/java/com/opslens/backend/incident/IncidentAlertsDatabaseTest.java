package com.opslens.backend.incident;

import static org.junit.jupiter.api.Assertions.*;

import com.opslens.backend.incident.IncidentRepository.AlertOutcome;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;

/** 같은 서비스의 알림을 열린 인시던트 하나로 묶고, 알림이 모두 해제될 때 닫는다 (V19, SPEC-compound-faults 5장). */
@EnabledIfEnvironmentVariable(named = "OPSLENS_TEST_DATABASE_URL", matches = ".+")
class IncidentAlertsDatabaseTest {
    private Connection connection;
    private JdbcClient jdbc;
    private IncidentRepository incidents;
    private String schema;

    @BeforeEach
    void setup() throws Exception {
        connection = DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"), "postgres", "");
        jdbc = JdbcClient.create(new SingleConnectionDataSource(connection, true));
        schema = "incident_alerts_test_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.sql("CREATE SCHEMA " + schema).update();
        connection.setSchema(schema);
        jdbc.sql("""
                CREATE TABLE incidents (id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, service TEXT,
                  title TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', fingerprint TEXT,
                  report TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ)
                """).update();
        jdbc.sql("""
                CREATE TABLE incident_alerts (id BIGSERIAL PRIMARY KEY, incident_id BIGINT NOT NULL REFERENCES incidents (id),
                  fingerprint TEXT NOT NULL, alertname TEXT, title TEXT NOT NULL, severity TEXT NOT NULL,
                  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ)
                """).update();
        jdbc.sql("CREATE UNIQUE INDEX ON incident_alerts (fingerprint) WHERE resolved_at IS NULL").update();
        jdbc.sql("CREATE TABLE ai_tasks (incident_id BIGINT, type TEXT, status TEXT)").update();
        incidents = new IncidentRepository(jdbc);
    }

    @AfterEach
    void cleanup() throws Exception {
        if (connection != null) {
            try {
                if (schema != null) jdbc.sql("DROP SCHEMA " + schema + " CASCADE").update();
            } finally {
                connection.close();
            }
        }
    }

    private AlertOutcome fire(String service, String alertname, String severity, String fingerprint) {
        return incidents.openOrAttach("tenant-a", service, alertname, service + " " + alertname, severity, fingerprint);
    }

    private String status(long id) {
        return jdbc.sql("SELECT status FROM incidents WHERE id = ?").param(id).query(String.class).single();
    }

    @Test
    void secondAlertOfTheSameServiceJoinsTheOpenIncidentAndRaisesSeverity() {
        AlertOutcome latency = fire("order-api", "HighLatency", "P2", "fp-latency");
        AlertOutcome error = fire("order-api", "HighErrorRate", "P1", "fp-error");
        assertEquals(new AlertOutcome(latency.incidentId(), true, false), latency);
        assertEquals(new AlertOutcome(latency.incidentId(), false, true), error);
        assertEquals("P1", jdbc.sql("SELECT severity FROM incidents WHERE id = ?").param(latency.incidentId())
                .query(String.class).single());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM incidents").query(Long.class).single());
    }

    @Test
    void repeatedAlertAndOtherServiceAreNotMerged() {
        AlertOutcome first = fire("order-api", "HighErrorRate", "P1", "fp-error");
        assertEquals(new AlertOutcome(first.incidentId(), false, false), fire("order-api", "HighErrorRate", "P1", "fp-error"));
        AlertOutcome other = fire("payment-api", "HighErrorRate", "P1", "fp-payment");
        assertTrue(other.opened());
        assertNotEquals(first.incidentId(), other.incidentId());
        // 서비스 라벨이 없는 알림은 묶을 기준이 없다
        assertTrue(fire(null, "HighErrorRate", "P1", "fp-a").opened());
        assertTrue(fire(null, "HighLatency", "P2", "fp-b").opened());
    }

    @Test
    void incidentClosesOnlyWhenEveryAttachedAlertIsResolved() {
        long id = fire("order-api", "HighErrorRate", "P1", "fp-error").incidentId();
        fire("order-api", "HighLatency", "P2", "fp-latency");
        incidents.resolveByFingerprint("fp-error");
        assertEquals("OPEN", status(id));
        incidents.resolveByFingerprint("fp-latency");
        assertEquals("RESOLVED", status(id));
        // 닫힌 뒤 같은 알림이 다시 울리면 새 인시던트다 (진단 작업도 끝났다)
        AlertOutcome again = fire("order-api", "HighErrorRate", "P1", "fp-error");
        assertTrue(again.opened());
        assertNotEquals(id, again.incidentId());
    }

    @Test
    void alertJoinsAResolvedIncidentWhoseDiagnosisIsStillRunning() {
        long id = fire("order-api", "HighErrorRate", "P1", "fp-error").incidentId();
        jdbc.sql("INSERT INTO ai_tasks VALUES (?, 'DIAGNOSE', 'RUNNING')").param(id).update();
        incidents.resolveByFingerprint("fp-error");   // 재시작으로 에러율 알림이 풀렸다
        assertEquals("RESOLVED", status(id));
        AlertOutcome latency = fire("order-api", "HighLatency", "P2", "fp-latency");   // 가려져 있던 결제 지연
        assertEquals(new AlertOutcome(id, false, true), latency);
        assertEquals("OPEN", status(id));
        jdbc.sql("UPDATE ai_tasks SET status = 'DONE'").update();
        incidents.resolveByFingerprint("fp-latency");
        assertEquals("RESOLVED", status(id));
        assertTrue(fire("order-api", "HighLatency", "P2", "fp-latency").opened());
    }
}

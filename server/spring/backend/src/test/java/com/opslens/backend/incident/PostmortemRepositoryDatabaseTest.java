package com.opslens.backend.incident;

import static org.junit.jupiter.api.Assertions.*;

import java.sql.Connection;
import java.sql.DriverManager;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;

@EnabledIfEnvironmentVariable(named = "OPSLENS_TEST_DATABASE_URL", matches = ".+")
class PostmortemRepositoryDatabaseTest {
    private Connection connection;
    private JdbcClient jdbc;
    private IncidentRepository incidents;
    private String schema;

    @BeforeEach
    void setup() throws Exception {
        connection = DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"), "postgres", "");
        jdbc = JdbcClient.create(new SingleConnectionDataSource(connection, true));
        schema = "postmortem_test_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.sql("CREATE SCHEMA " + schema).update();
        connection.setSchema(schema);
        jdbc.sql("""
                CREATE TABLE incidents (id BIGINT PRIMARY KEY, tenant_id TEXT, service TEXT, title TEXT,
                  severity TEXT, status TEXT, report TEXT, started_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ)
                """).update();
        jdbc.sql("CREATE TABLE ai_tasks (incident_id BIGINT, tenant_id TEXT, status TEXT, answer TEXT)").update();
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

    private void insert(long id, String tenant, String status, String report, String taskStatus) {
        jdbc.sql("INSERT INTO incidents VALUES (?, ?, 'order-api', 'test', 'P2', ?, ?, now(), now())")
                .params(id, tenant, status, report).update();
        jdbc.sql("INSERT INTO ai_tasks VALUES (?, ?, ?, ?)").params(id, tenant, taskStatus, report).update();
    }

    @Test
    void onlyResolvedCompletedReportsForRequestedTenantAreExported() {
        insert(1, "tenant-a", "RESOLVED", "complete", "DONE");
        insert(2, "tenant-b", "RESOLVED", "other tenant", "DONE");
        insert(3, "tenant-a", "OPEN", "not closed", "DONE");
        insert(4, "tenant-a", "RESOLVED", "", "DONE");
        insert(5, "tenant-a", "RESOLVED", "failed", "FAILED");
        insert(6, "tenant-a", "RESOLVED", "stale report", "DONE");
        jdbc.sql("UPDATE ai_tasks SET answer='new answer' WHERE incident_id=6").update();
        assertEquals(List.of(1L), incidents.postmortems("tenant-a", 0).stream().map(Incident::id).toList());
        assertTrue(incidents.postmortems("tenant-a", 1).isEmpty());
    }

    @Test
    void paginationAndLateReportArrivalDoNotLoseOlderIncidents() {
        for (long id = 1; id <= 105; id++) insert(id, "tenant-a", "RESOLVED", "report " + id, "DONE");
        assertEquals(100, incidents.postmortems("tenant-a", 0).size());
        assertEquals(5, incidents.postmortems("tenant-a", 100).size());
        jdbc.sql("UPDATE incidents SET report='' WHERE id=1").update();
        assertEquals(2L, incidents.postmortems("tenant-a", 0).getFirst().id());
        jdbc.sql("UPDATE incidents SET report='report 1' WHERE id=1").update();
        assertEquals(1L, incidents.postmortems("tenant-a", 0).getFirst().id());
    }
}

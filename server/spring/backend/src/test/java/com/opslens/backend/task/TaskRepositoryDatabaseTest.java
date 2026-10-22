package com.opslens.backend.task;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.incident.IncidentRepository;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.web.server.ResponseStatusException;

/** 전용 임시 PostgreSQL에서 실행한다. 운영 DB나 개발 데이터에 연결하지 않는다. */
@EnabledIfEnvironmentVariable(named = "OPSLENS_TEST_DATABASE_URL", matches = ".+")
class TaskRepositoryDatabaseTest {
    private Connection connection;
    private JdbcClient jdbc;
    private TaskRepository tasks;
    private String schema;
    private final ObjectMapper json = new ObjectMapper();
    private final UUID id = UUID.randomUUID();

    @BeforeEach
    void setup() throws Exception {
        connection = DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"), "postgres", "");
        jdbc = JdbcClient.create(new SingleConnectionDataSource(connection, true));
        schema = "review_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.sql("CREATE SCHEMA " + schema).update();
        connection.setSchema(schema);
        jdbc.sql("""
                CREATE TABLE ai_tasks (id UUID PRIMARY KEY, status TEXT NOT NULL, pending_action JSONB,
                                       answer TEXT, sources JSONB, summary JSONB, trace_url TEXT,
                                       updated_at TIMESTAMPTZ NOT NULL DEFAULT now())
                """).update();
        jdbc.sql("CREATE TABLE incidents (id BIGINT PRIMARY KEY, tenant_id TEXT, report TEXT)").update();
        jdbc.sql("INSERT INTO ai_tasks (id,status,pending_action) VALUES (?,'WAITING_APPROVAL','{\"approvalId\":\"one\"}')")
                .param(id).update();
        tasks = new TaskRepository(jdbc, json);
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

    @Test
    void concurrentApprovalsHaveOneWinner() throws Exception {
        var expected = json.readTree("{\"approvalId\":\"one\"}");
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        try (var executor = Executors.newFixedThreadPool(2)) {
            java.util.concurrent.Callable<Boolean> claim = () -> {
                try (Connection c = DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"), "postgres", "")) {
                    c.setSchema(schema);
                    TaskRepository repo = new TaskRepository(JdbcClient.create(new SingleConnectionDataSource(c, true)), json);
                    ready.countDown();
                    if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("start timed out");
                    return repo.claimApproval(id, expected);
                }
            };
            var first = executor.submit(claim);
            var second = executor.submit(claim);
            try {
                assertTrue(ready.await(5, TimeUnit.SECONDS));
            } finally {
                start.countDown();
            }
            assertNotEquals(first.get(10, TimeUnit.SECONDS), second.get(10, TimeUnit.SECONDS));
        }
    }

    @Test
    void deliveryFailureRestoresOnlyTheSameApproval() throws Exception {
        var expected = json.readTree("{\"approvalId\":\"one\"}");
        assertTrue(tasks.claimApproval(id, expected));
        assertTrue(tasks.restoreApproval(id, expected));
        assertTrue(tasks.claimApproval(id, expected));
        jdbc.sql("UPDATE ai_tasks SET status='WAITING_APPROVAL', pending_action='{\"approvalId\":\"two\"}' WHERE id=?")
                .param(id).update();
        assertFalse(tasks.restoreApproval(id, expected));
        tasks.clearApproval(id, expected);
        assertEquals("two", jdbc.sql("SELECT pending_action->>'approvalId' FROM ai_tasks WHERE id=?")
                .param(id).query(String.class).single());
    }

    @Test
    void changeRestoreKeepsTheOriginalWaitStart() throws Exception {
        var change = json.readTree("{\"prNumber\":7}");
        var since = java.time.OffsetDateTime.parse("2026-10-23T01:00:00Z");
        jdbc.sql("UPDATE ai_tasks SET status='CHANGE_PENDING', pending_action=?::jsonb, updated_at=? WHERE id=?")
                .params(change.toString(), since, id).update();
        assertTrue(tasks.claimChange(id));
        assertTrue(tasks.restoreChange(id, change, since));
        assertFalse(tasks.restoreChange(id, change, since));   // 이미 되돌렸다
        assertEquals(1, jdbc.sql("SELECT count(*) FROM ai_tasks WHERE id=? AND status='CHANGE_PENDING' AND updated_at=?"
                + " AND pending_action->>'prNumber'='7'").params(id, since).query(Integer.class).single());
    }

    @Test
    void finishedTaskIsNotReopenedByLateEvents() throws Exception {
        var action = json.readTree("{\"approvalId\":\"late\"}");
        assertTrue(tasks.complete(id, "FAILED", "dispatch failed", null, null, null));
        assertFalse(tasks.complete(id, "DONE", "late", null, null, null));
        assertFalse(tasks.waitForApproval(id, action));
        assertFalse(tasks.waitForChange(id, action));
        assertEquals("FAILED", jdbc.sql("SELECT status FROM ai_tasks WHERE id=?").param(id).query(String.class).single());
    }

    @Test
    void reportSaveNeverCrossesTenantBoundary() {
        jdbc.sql("INSERT INTO incidents VALUES (1, 'tenant-b', 'original')").update();
        IncidentRepository incidents = new IncidentRepository(jdbc);
        assertThrows(ResponseStatusException.class, () -> incidents.saveReport("tenant-a", 1L, "wrong"));
        assertEquals("original", jdbc.sql("SELECT report FROM incidents WHERE id=1").query(String.class).single());
        incidents.saveReport("tenant-b", 1L, "updated");
        assertEquals("updated", jdbc.sql("SELECT report FROM incidents WHERE id=1").query(String.class).single());
    }
}

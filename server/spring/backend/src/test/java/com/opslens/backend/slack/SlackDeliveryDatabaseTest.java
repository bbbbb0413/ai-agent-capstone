package com.opslens.backend.slack;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.incident.IncidentRepository;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

/** 외부 Slack 호출은 대역으로 바꾸고 승인·대기열·발송 상태를 실제 DB에서 검증한다. */
@EnabledIfEnvironmentVariable(named = "OPSLENS_TEST_DATABASE_URL", matches = ".+")
class SlackDeliveryDatabaseTest {
    private String schema;
    private JdbcClient jdbc;
    private TransactionTemplate tx;
    private SlackConnectionService connections;
    private SlackDeliveryService deliveries;
    private SlackWebhookClient client;
    private SlackRelay relay;
    private final CurrentUser admin = new CurrentUser(1, "admin", "MSP_ADMIN", List.of("tenant-a"));
    private final CurrentUser engineer = new CurrentUser(2, "engineer", "MSP_ENGINEER", List.of("tenant-a"));
    private final String webhook = "https://hooks.slack.com/services/TTEST/BTEST/placeholder";

    @BeforeEach
    void setup() throws Exception {
        schema = "slack_test_" + UUID.randomUUID().toString().replace("-", "");
        String url = System.getenv("OPSLENS_TEST_DATABASE_URL");
        try (Connection c = DriverManager.getConnection(url, "postgres", "")) {
            c.createStatement().execute("CREATE SCHEMA " + schema);
        }
        var ds = new DriverManagerDataSource(url + (url.contains("?") ? "&" : "?") + "currentSchema=" + schema,
                "postgres", "");
        jdbc = JdbcClient.create(ds);
        tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        jdbc.sql("CREATE TABLE tenants(id TEXT PRIMARY KEY)").update();
        jdbc.sql("INSERT INTO tenants VALUES ('tenant-a'), ('tenant-b')").update();
        jdbc.sql("""
                CREATE TABLE incidents(id BIGINT PRIMARY KEY, tenant_id TEXT, service TEXT, title TEXT,
                    severity TEXT, status TEXT, report TEXT, started_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ)
                """).update();
        jdbc.sql("INSERT INTO incidents VALUES (1,'tenant-a','order-api','test','P2','OPEN',NULL,now(),NULL),"
                + "(2,'tenant-b','other','test','P2','OPEN',NULL,now(),NULL)").update();
        jdbc.sql("CREATE TABLE escalations(id BIGINT PRIMARY KEY, tenant_id TEXT, incident_id BIGINT, message TEXT)").update();
        jdbc.sql("INSERT INTO escalations VALUES (1,'tenant-a',1,'원본 호출 문안'), (2,'tenant-b',2,'비공개 문안')").update();
        new ResourceDatabasePopulator(new ClassPathResource("db/migration/V13__slack_notifications.sql")).execute(ds);
        AuditLog audit = mock(AuditLog.class);
        var secrets = new SlackSecretBox(mock(OpsLensProperties.class), "test-only-encryption-key");
        connections = new SlackConnectionService(jdbc, secrets, audit);
        deliveries = new SlackDeliveryService(jdbc, new IncidentRepository(jdbc), connections, audit);
        client = mock(SlackWebhookClient.class);
        relay = new SlackRelay(deliveries, connections, client);
    }

    @AfterEach
    void cleanup() throws Exception {
        if (relay != null) relay.stop();
        if (schema != null) try (Connection c = DriverManager.getConnection(System.getenv("OPSLENS_TEST_DATABASE_URL"), "postgres", "")) {
            c.createStatement().execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    private void connect() {
        tx.executeWithoutResult(s -> connections.save("tenant-a",
                new SlackConnectionService.Update("#ops-a", webhook, true, 0), admin));
    }

    private void approve() {
        tx.executeWithoutResult(s -> deliveries.approve(1, 1,
                new SlackDeliveryService.Approval("검토하고 수정한 문안", 1), engineer));
    }

    @Test
    void savingConnectionDoesNotSendOldMessagesButApprovalSendsExactReviewedTextOnce() {
        assertFalse(deliveries.preview(1, 1, engineer).connection().configured());
        connect();
        relay.sendPending();
        verifyNoInteractions(client);
        String stored = jdbc.sql("SELECT webhook_encrypted FROM slack_connections").query(String.class).single();
        assertFalse(stored.contains(webhook));
        approve();
        assertThrows(ResponseStatusException.class, this::approve);
        when(client.send(webhook, "검토하고 수정한 문안")).thenReturn(new SlackWebhookClient.Result("SENT", null, 0));
        relay.sendPending();
        relay.sendPending();
        verify(client, times(1)).send(webhook, "검토하고 수정한 문안");
        var delivery = deliveries.list(1, engineer).getFirst();
        assertEquals("SENT", delivery.status());
        assertEquals("engineer", delivery.approvedBy());
        assertNotNull(delivery.sentAt());
    }

    @Test
    void settingsChangeInvalidatesOldPreviewAndCancelsQueuedMessages() {
        connect();
        approve();
        tx.executeWithoutResult(s -> connections.save("tenant-a",
                new SlackConnectionService.Update("#new-channel", null, true, 1), admin));
        assertThrows(ResponseStatusException.class, this::approve);
        assertEquals("CANCELLED", deliveries.list(1, engineer).getFirst().status());
        relay.sendPending();
        verifyNoInteractions(client);
        tx.executeWithoutResult(s -> deliveries.approve(1, 1,
                new SlackDeliveryService.Approval("새 채널로 재검토", 2), engineer));
        assertEquals("QUEUED", deliveries.list(1, engineer).getFirst().status());
    }

    @Test
    void viewerAndOtherTenantCannotPreviewOrApprove() {
        var viewer = new CurrentUser(3, "viewer", "TENANT_VIEWER", List.of("tenant-a"));
        assertThrows(ResponseStatusException.class, () -> deliveries.preview(1, 1, viewer));
        assertThrows(ResponseStatusException.class, () -> deliveries.preview(2, 2, engineer));
        assertThrows(ResponseStatusException.class, () -> deliveries.preview(1, 2, engineer));
        assertThrows(ResponseStatusException.class, () -> deliveries.approve(1, 1,
                new SlackDeliveryService.Approval("x", 1), viewer));
        assertThrows(ResponseStatusException.class, this::approve); // 연결 전에는 발송 불가
    }

    @Test
    void rateLimitWaitsButUnknownDeliveryNeverRetriesAutomatically() {
        connect();
        approve();
        when(client.send(anyString(), anyString())).thenReturn(new SlackWebhookClient.Result("QUEUED", "제한", 60));
        relay.sendPending();
        relay.sendPending();
        verify(client, times(1)).send(anyString(), anyString());
        assertEquals("QUEUED", deliveries.list(1, engineer).getFirst().status());
        jdbc.sql("UPDATE slack_deliveries SET available_at=now()").update();
        when(client.send(anyString(), anyString())).thenReturn(new SlackWebhookClient.Result("UNKNOWN", "확인 필요", 0));
        relay.sendPending();
        relay.sendPending();
        verify(client, times(2)).send(anyString(), anyString());
        assertEquals("UNKNOWN", deliveries.list(1, engineer).getFirst().status());
        assertThrows(ResponseStatusException.class, this::approve);
    }

    @Test
    void concurrentApprovalsAndWorkersClaimOnlyOneDelivery() throws Exception {
        connect();
        try (var pool = Executors.newFixedThreadPool(2)) {
            java.util.concurrent.Callable<Boolean> operation = () -> {
                try { approve(); return true; } catch (ResponseStatusException e) { return false; }
            };
            var first = pool.submit(operation);
            var second = pool.submit(operation);
            assertNotEquals(first.get(), second.get());
            var firstClaim = pool.submit(() -> deliveries.claim().size());
            var secondClaim = pool.submit(() -> deliveries.claim().size());
            assertEquals(1, firstClaim.get() + secondClaim.get());
        }
        jdbc.sql("UPDATE slack_deliveries SET claimed_at=now()-interval '3 minutes'").update();
        assertTrue(deliveries.claim().isEmpty());
        assertEquals("UNKNOWN", deliveries.list(1, engineer).getFirst().status());
    }
}

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
    private SlackTrackingSettings trackingSettings;
    private SlackTrackingService tracking;
    private SlackSecretBox secrets;
    private final com.fasterxml.jackson.databind.ObjectMapper json = new com.fasterxml.jackson.databind.ObjectMapper();
    private static final String SIGNING_SECRET = "0123456789abcdef0123456789abcdef";
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
        jdbc.sql("CREATE TABLE users(id BIGINT PRIMARY KEY, username TEXT UNIQUE, role TEXT)").update();
        jdbc.sql("CREATE TABLE user_tenants(user_id BIGINT, tenant_id TEXT)").update();
        new ResourceDatabasePopulator(new ClassPathResource("db/migration/V14__slack_tracking.sql")).execute(ds);
        AuditLog audit = mock(AuditLog.class);
        secrets = new SlackSecretBox(mock(OpsLensProperties.class), "test-only-encryption-key");
        connections = new SlackConnectionService(jdbc, secrets, audit);
        deliveries = new SlackDeliveryService(jdbc, new IncidentRepository(jdbc), connections, audit);
        client = mock(SlackWebhookClient.class);
        relay = new SlackRelay(deliveries, connections, client);
        trackingSettings = new SlackTrackingSettings(jdbc, secrets, connections, audit);
        tracking = new SlackTrackingService(jdbc, trackingSettings, connections, secrets, json, audit);
        jdbc.sql("INSERT INTO users VALUES(1,'admin','MSP_ADMIN'),(2,'engineer','MSP_ENGINEER'),(3,'other','MSP_ENGINEER'),(4,'viewer','TENANT_VIEWER')").update();
        jdbc.sql("INSERT INTO user_tenants VALUES(2,'tenant-a'),(3,'tenant-a'),(4,'tenant-a')").update();
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

    private UUID trackedDelivery() {
        connect();
        tx.executeWithoutResult(s -> trackingSettings.save("tenant-a", new SlackTrackingSettings.Update(
            true,"ATEST01","TTEST01","CTEST01",SIGNING_SECRET,0,
            List.of(new SlackTrackingSettings.Member("UTEST01","engineer"))),admin));
        tx.executeWithoutResult(s -> deliveries.approve(1,1,new SlackDeliveryService.Approval("추적 알림",2),engineer));
        var id=deliveries.list(1,engineer).getFirst().id();
        jdbc.sql("UPDATE slack_deliveries SET status='SENT',sent_at=now() WHERE id=?").param(id).update();
        return id;
    }

    private byte[] interaction(UUID id,String action,String user,String team,String responseUrl) throws Exception {
        var payload=java.util.Map.of("type","block_actions","api_app_id","ATEST01",
            "team",java.util.Map.of("id",team),"channel",java.util.Map.of("id","CTEST01"),
            "user",java.util.Map.of("id",user),"container",java.util.Map.of("type","message","message_ts","1234567890.123456"),
            "response_url",responseUrl,"actions",List.of(java.util.Map.of("action_id","opslens_"+action,"value",id.toString())));
        return ("payload="+java.net.URLEncoder.encode(json.writeValueAsString(payload),java.nio.charset.StandardCharsets.UTF_8))
            .getBytes(java.nio.charset.StandardCharsets.UTF_8);
    }
    private byte[] interaction(UUID id,String action) throws Exception {
        return interaction(id,action,"UTEST01","TTEST01","https://hooks.slack.com/actions/TTEST/BTEST/placeholder");
    }
    static String signature(byte[] raw,String timestamp) throws Exception {
        var mac=javax.crypto.Mac.getInstance("HmacSHA256");
        mac.init(new javax.crypto.spec.SecretKeySpec(SIGNING_SECRET.getBytes(java.nio.charset.StandardCharsets.UTF_8),"HmacSHA256"));
        mac.update(("v0:"+timestamp+":").getBytes(java.nio.charset.StandardCharsets.UTF_8));
        return "v0="+java.util.HexFormat.of().formatHex(mac.doFinal(raw));
    }
    private void click(byte[] raw) throws Exception {
        String timestamp=Long.toString(java.time.Instant.now().getEpochSecond());
        String signed=signature(raw,timestamp);
        tx.executeWithoutResult(s -> tracking.interact(raw,timestamp,signed));
    }

    @Test
    void signedSlackActionsTrackActorAndTimeAndDeduplicateRetriesWithoutResolvingIncident() throws Exception {
        UUID id=trackedDelivery();
        byte[] ack=interaction(id,"ack");
        click(ack); click(ack);
        assertEquals(1,tracking.list(1,engineer).getFirst().events().size());
        click(interaction(id,"start"));
        click(interaction(id,"complete"));
        var result=tracking.list(1,engineer).getFirst();
        assertEquals("COMPLETED",result.status());
        assertEquals("engineer",result.acknowledgedBy());
        assertEquals("engineer",result.assignedTo());
        assertEquals("engineer",result.completedBy());
        assertNotNull(result.completedAt());
        assertEquals(List.of("ACK","START","COMPLETE"),result.events().stream().map(SlackTrackingService.Event::action).toList());
        assertTrue(result.events().stream().allMatch(e -> "SLACK".equals(e.source())));
        assertEquals("OPEN",jdbc.sql("SELECT status FROM incidents WHERE id=1").query(String.class).single());
        assertEquals(3,jdbc.sql("SELECT count(*) FROM slack_tracking_updates").query(Integer.class).single());
        assertFalse(jdbc.sql("SELECT url_encrypted FROM slack_tracking_updates LIMIT 1").query(String.class).single().contains("hooks.slack.com"));
        assertFalse(jdbc.sql("SELECT signing_secret_encrypted FROM slack_tracking_settings").query(String.class).single().contains(SIGNING_SECRET));
        assertFalse(json.writeValueAsString(trackingSettings.view("tenant-a",admin)).contains(SIGNING_SECRET));
    }

    @Test
    void invalidSignatureStaleTimestampForeignWorkspaceAndUnsafeResponseUrlDoNotChangeState() throws Exception {
        UUID id=trackedDelivery(); byte[] raw=interaction(id,"ack");
        String now=Long.toString(java.time.Instant.now().getEpochSecond());
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.interact(raw,now,"v0="+"0".repeat(64))));
        String stale=Long.toString(java.time.Instant.now().minusSeconds(301).getEpochSecond());
        String signed=signature(raw,stale);
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.interact(raw,stale,signed)));
        assertThrows(ResponseStatusException.class,()->click(interaction(id,"ack","UTEST01","TOTHER1","https://hooks.slack.com/actions/T/B/x")));
        assertThrows(ResponseStatusException.class,()->click(interaction(id,"ack","UTEST01","TTEST01","https://127.0.0.1/actions/T/B/x")));
        assertEquals("WAITING",tracking.list(1,engineer).getFirst().status());
        assertEquals(0,jdbc.sql("SELECT count(*) FROM slack_tracking_updates").query(Integer.class).single());
    }

    @Test
    void unmappedSlackUserReceivesPrivateFeedbackWithoutTakingOwnership() throws Exception {
        UUID id=trackedDelivery();
        click(interaction(id,"start","UOTHER1","TTEST01","https://hooks.slack.com/actions/T/B/x"));
        assertEquals("WAITING",tracking.list(1,engineer).getFirst().status());
        assertTrue(tracking.list(1,engineer).getFirst().events().isEmpty());
        String feedback=jdbc.sql("SELECT feedback FROM slack_tracking_updates").query(String.class).single();
        assertEquals("ephemeral",tracking.updatePayload(id,feedback).get("response_type"));
    }

    @Test
    void webActionsEnforceTenantRoleAndCurrentAssignee() {
        UUID id=trackedDelivery();
        var other=new CurrentUser(3,"other","MSP_ENGINEER",List.of("tenant-a"));
        assertThrows(ResponseStatusException.class,()->tracking.list(1,new CurrentUser(4,"viewer","TENANT_VIEWER",List.of("tenant-a"))));
        assertThrows(ResponseStatusException.class,()->tracking.list(1,new CurrentUser(5,"foreign","MSP_ENGINEER",List.of("tenant-b"))));
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.act(2,id,"START",engineer)));
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.act(1,id,"COMPLETE",engineer)));
        tx.executeWithoutResult(s->tracking.act(1,id,"START",engineer));
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.act(1,id,"START",other)));
        assertThrows(ResponseStatusException.class,()->tx.executeWithoutResult(s->tracking.act(1,id,"COMPLETE",other)));
        tx.executeWithoutResult(s->tracking.act(1,id,"COMPLETE",admin));
        assertEquals("admin",tracking.list(1,engineer).getFirst().completedBy());
        assertEquals(2,tracking.list(1,engineer).getFirst().events().size());
    }

    @Test
    void simultaneousClaimHasOnlyOneOwnerAndSettingsChangeInvalidatesOldButtons() throws Exception {
        UUID id=trackedDelivery();
        var other=new CurrentUser(3,"other","MSP_ENGINEER",List.of("tenant-a"));
        try(var pool=Executors.newFixedThreadPool(2)) {
            java.util.concurrent.Callable<Boolean> first=()->{try {tx.executeWithoutResult(s->tracking.act(1,id,"START",engineer));return true;}catch(ResponseStatusException e){return false;}};
            java.util.concurrent.Callable<Boolean> second=()->{try {tx.executeWithoutResult(s->tracking.act(1,id,"START",other));return true;}catch(ResponseStatusException e){return false;}};
            var a=pool.submit(first);var b=pool.submit(second);
            assertNotEquals(a.get(),b.get());
        }
        assertEquals(1,tracking.list(1,engineer).getFirst().events().size());
        tx.executeWithoutResult(s->trackingSettings.save("tenant-a",new SlackTrackingSettings.Update(true,"ATEST01","TTEST01","CTEST01",null,1,List.of(new SlackTrackingSettings.Member("UTEST01","engineer"))),admin));
        assertThrows(ResponseStatusException.class,()->click(interaction(id,"complete")));
        assertEquals("IN_PROGRESS",tracking.list(1,engineer).getFirst().status());
    }

    @Test
    @SuppressWarnings("unchecked")
    void asynchronousRelayReplacesOriginalWithLatestStateAndNeverPostsToWebhook() throws Exception {
        UUID id=trackedDelivery();
        click(interaction(id,"start"));
        var http=mock(java.net.http.HttpClient.class);
        java.net.http.HttpResponse<String> response=mock(java.net.http.HttpResponse.class);
        when(response.statusCode()).thenReturn(200);when(response.body()).thenReturn("ok");
        when(http.send(any(java.net.http.HttpRequest.class),any(java.net.http.HttpResponse.BodyHandler.class))).thenReturn(response);
        var worker=new SlackTrackingRelay(jdbc,secrets,tracking,json,http);
        try { worker.sendPending();worker.sendPending(); } finally {worker.stop();}
        var request=org.mockito.ArgumentCaptor.forClass(java.net.http.HttpRequest.class);
        verify(http,times(1)).send(request.capture(),any(java.net.http.HttpResponse.BodyHandler.class));
        assertTrue(request.getValue().uri().getPath().startsWith("/actions/"));
        assertEquals("SENT",jdbc.sql("SELECT status FROM slack_tracking_updates").query(String.class).single());
        var payload=tracking.updatePayload(id,null);
        assertEquals(true,payload.get("replace_original"));
        assertTrue(json.writeValueAsString(payload).contains("담당 처리 중"));
        verifyNoInteractions(client);
    }
}

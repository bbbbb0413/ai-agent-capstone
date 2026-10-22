package com.opslens.backend.slack;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.config.OpsLensProperties;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.server.ResponseStatusException;

class SlackSafetyTest {
    @Test
    void secretsAreRandomizedAuthenticatedAndTenantBound() {
        var box = new SlackSecretBox(mock(OpsLensProperties.class), "test-encryption-key");
        String plain = "https://hooks.slack.com/services/TTEST/BTEST/secret";
        String cipher = box.encrypt("tenant-a", plain);
        assertFalse(cipher.contains(plain));
        assertNotEquals(cipher, box.encrypt("tenant-a", plain));
        assertEquals(plain, box.decrypt("tenant-a", cipher));
        assertThrows(IllegalStateException.class, () -> box.decrypt("tenant-b", cipher));
        var other = new SlackSecretBox(mock(OpsLensProperties.class), "different-encryption-key");
        assertThrows(IllegalStateException.class, () -> other.decrypt("tenant-a", cipher));
    }

    @Test
    void onlySlackHttpsWebhookUrlsAreAccepted() {
        SlackConnectionService.validateWebhook("https://hooks.slack.com/services/TTEST/BTEST/token");
        for (String url : List.of("http://hooks.slack.com/services/T/B/token", "https://127.0.0.1/services/T/B/token",
                "https://hooks.slack.com.attacker.example/services/T/B/token", "https://hooks.slack.com@evil.example/",
                "https://hooks.slack.com/services/T/B/token?secret=x", "https://hooks.slack.com:444/services/T/B/token"))
            assertThrows(ResponseStatusException.class, () -> SlackConnectionService.validateWebhook(url));
    }

    @Test
    void onlyAdminCanSaveConnectionAndOtherTenantsAreBlocked() {
        JdbcClient jdbc = mock(JdbcClient.class);
        var service = new SlackConnectionService(jdbc, mock(SlackSecretBox.class), mock(AuditLog.class));
        var request = new SlackConnectionService.Update("#test", "secret", true, 0);
        var engineer = new CurrentUser(2, "engineer", "MSP_ENGINEER", List.of("tenant-a"));
        var admin = new CurrentUser(1, "admin", "MSP_ADMIN", List.of("tenant-a"));
        assertThrows(ResponseStatusException.class, () -> service.save("tenant-a", request, engineer));
        assertThrows(ResponseStatusException.class, () -> service.save("tenant-b", request, admin));
        verifyNoInteractions(jdbc);
    }

    @Test
    void secretIsNeverIncludedInSettingsView() throws Exception {
        String json = new ObjectMapper().writeValueAsString(new SlackConnectionService.View(true, true, "#test", 1));
        assertFalse(json.contains("webhook"));
        assertFalse(json.contains("encrypted"));
    }

    @Test
    void successRateLimitAndAmbiguousResultsAreDistinct() {
        assertEquals("SENT", SlackWebhookClient.classify(200, "ok", "").status());
        var rate = SlackWebhookClient.classify(429, "rate_limited", "45");
        assertEquals("QUEUED", rate.status());
        assertEquals(45, rate.retryAfterSeconds());
        assertEquals("FAILED", SlackWebhookClient.classify(403, "invalid_token", "").status());
        assertEquals("UNKNOWN", SlackWebhookClient.classify(500, "server error", "").status());
        assertEquals("UNKNOWN", SlackWebhookClient.classify(200, "unexpected", "").status());
    }

    @Test
    void reviewedMessageIsPlainTextWithoutBroadcastMentions() throws Exception {
        var payload = new ObjectMapper().valueToTree(SlackWebhookClient.payload("원인 <!channel> <@U123> &"));
        assertEquals("plain_text", payload.at("/blocks/0/text/type").asText());
        assertEquals("원인 <!channel> <@U123> &", payload.at("/blocks/0/text/text").asText());
        assertFalse(payload.get("text").asText().contains("<!channel>"));
    }
}

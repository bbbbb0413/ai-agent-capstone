package com.opslens.backend.slack;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import java.net.URI;
import java.util.Map;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class SlackConnectionService {
    public record View(boolean configured, boolean enabled, String channelLabel, long version) {}
    public record Update(String channelLabel, String webhookUrl, boolean enabled, long version) {}
    record Connection(String tenantId, String channelLabel, String encrypted, boolean enabled, long version) {}

    private final JdbcClient jdbc;
    private final SlackSecretBox secrets;
    private final AuditLog audit;

    public SlackConnectionService(JdbcClient jdbc, SlackSecretBox secrets, AuditLog audit) {
        this.jdbc = jdbc;
        this.secrets = secrets;
        this.audit = audit;
    }

    Optional<Connection> find(String tenant) {
        return jdbc.sql("SELECT * FROM slack_connections WHERE tenant_id=?").param(tenant)
                .query((r, n) -> new Connection(tenant, r.getString("channel_label"), r.getString("webhook_encrypted"),
                        r.getBoolean("enabled"), r.getLong("version"))).optional();
    }

    public View view(String tenant, CurrentUser user) {
        user.requireMspStaff();
        user.requireAccess(tenant);
        return find(tenant).map(c -> new View(true, c.enabled(), c.channelLabel(), c.version()))
                .orElse(new View(false, false, "", 0));
    }

    @Transactional
    public View save(String tenant, Update update, CurrentUser user) {
        user.requireAccess(tenant);
        if (!user.isAdmin()) throw new ResponseStatusException(HttpStatus.FORBIDDEN, "MSP_ADMIN only");
        lock(tenant);
        if (update.channelLabel() == null || update.channelLabel().isBlank() || update.channelLabel().length() > 100)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Slack 채널 이름을 100자 이내로 입력한다");
        Connection old = find(tenant).orElse(null);
        String encrypted = old == null ? null : old.encrypted();
        if (update.webhookUrl() != null && !update.webhookUrl().isBlank()) {
            validateWebhook(update.webhookUrl().trim());
            encrypted = secrets.encrypt(tenant, update.webhookUrl().trim());
        }
        if (encrypted == null) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Incoming Webhook URL이 필요하다");
        int count = jdbc.sql("""
                INSERT INTO slack_connections(tenant_id, channel_label, webhook_encrypted, enabled, updated_by)
                SELECT ?, ?, ?, ?, ? WHERE ? = 0
                ON CONFLICT (tenant_id) DO NOTHING
                """).params(tenant, update.channelLabel().trim(), encrypted, update.enabled(), user.username(),
                        update.version()).update();
        if (count == 0) count = jdbc.sql("""
                UPDATE slack_connections SET channel_label=?, webhook_encrypted=?, enabled=?, version=version+1,
                    updated_by=?, updated_at=now() WHERE tenant_id=? AND version=?
                """).params(update.channelLabel().trim(), encrypted, update.enabled(), user.username(), tenant,
                        update.version()).update();
        if (count != 1) throw new ResponseStatusException(HttpStatus.CONFLICT, "설정이 변경되었다. 새로고침 후 다시 저장한다");
        // 이미 승인된 문안을 새 채널로 돌리지 않는다. 발송 중인 요청은 이미 외부로 전달됐을 수 있다.
        jdbc.sql("UPDATE slack_deliveries SET status='CANCELLED', last_error='Slack 설정 변경으로 취소됨' "
                + "WHERE tenant_id=? AND status='QUEUED'").param(tenant).update();
        audit.record(tenant, user.username(), "SLACK_CONNECTION_UPDATED", Map.of("enabled", update.enabled()), null);
        return view(tenant, user);
    }

    String webhook(Connection connection) {
        String url = secrets.decrypt(connection.tenantId(), connection.encrypted());
        validateWebhook(url);
        return url;
    }

    void lock(String tenant) {
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))")
                .param("slack-connection:" + tenant).query().listOfRows();
    }

    static void validateWebhook(String url) {
        try {
            URI uri = URI.create(url);
            if ("https".equals(uri.getScheme()) && "hooks.slack.com".equals(uri.getHost())
                    && uri.getPort() == -1 && uri.getUserInfo() == null && uri.getQuery() == null
                    && uri.getFragment() == null && uri.getPath().matches("/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+"))
                return;
        } catch (IllegalArgumentException ignored) { }
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "올바른 Slack Incoming Webhook URL을 입력한다");
    }
}

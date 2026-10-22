package com.opslens.backend.slack;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.incident.Incident;
import com.opslens.backend.incident.IncidentRepository;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class SlackDeliveryService {
    public record Preview(long escalationId, String tenantId, SlackConnectionService.View connection, String message) {}
    public record Approval(String message, long connectionVersion) {}
    public record Delivery(UUID id, long escalationId, String channelLabel, String message, String status,
            String approvedBy, int attempts, String lastError, OffsetDateTime sentAt) {}
    record Claimed(UUID id, String tenantId, String message, long connectionVersion, int attempts) {}

    private final JdbcClient jdbc;
    private final IncidentRepository incidents;
    private final SlackConnectionService connections;
    private final AuditLog audit;

    public SlackDeliveryService(JdbcClient jdbc, IncidentRepository incidents, SlackConnectionService connections,
            AuditLog audit) {
        this.jdbc = jdbc;
        this.incidents = incidents;
        this.connections = connections;
        this.audit = audit;
    }

    private Incident access(long incidentId, CurrentUser user) {
        user.requireMspStaff();
        Incident incident = incidents.get(incidentId);
        user.requireAccess(incident.tenantId());
        return incident;
    }

    public Preview preview(long incidentId, long escalationId, CurrentUser user) {
        Incident incident = access(incidentId, user);
        String message = jdbc.sql("SELECT message FROM escalations WHERE id=? AND incident_id=? AND tenant_id=?")
                .params(escalationId, incidentId, incident.tenantId()).query(String.class).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "호출 기록이 없다"));
        return new Preview(escalationId, incident.tenantId(), connections.view(incident.tenantId(), user), message);
    }

    @Transactional
    public void approve(long incidentId, long escalationId, Approval approval, CurrentUser user) {
        Incident incident = access(incidentId, user);
        // 설정 변경과 발송 승인을 직렬화한다. 검토한 채널 버전이 그대로일 때만 발송 대기열에 들어간다.
        connections.lock(incident.tenantId());
        Preview preview = preview(incidentId, escalationId, user);
        if (!preview.connection().enabled() || approval.connectionVersion() != preview.connection().version())
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Slack 연결이 꺼져 있거나 변경되었다. 미리보기를 다시 연다");
        if (approval.message() == null || approval.message().isBlank() || approval.message().length() > 2800)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "메시지를 1~2,800자로 입력한다");
        UUID id = UUID.randomUUID();
        int changed = jdbc.sql("""
                INSERT INTO slack_deliveries(id, tenant_id, escalation_id, channel_label, connection_version,
                                             message, approved_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (escalation_id) DO UPDATE SET channel_label=EXCLUDED.channel_label,
                  connection_version=EXCLUDED.connection_version, message=EXCLUDED.message,
                  approved_by=EXCLUDED.approved_by, approved_at=now(), status='QUEUED', attempts=0,
                  available_at=now(), claimed_at=NULL, last_error=NULL
                WHERE slack_deliveries.status IN ('FAILED', 'CANCELLED')
                """).params(id, incident.tenantId(), escalationId, preview.connection().channelLabel(),
                        approval.connectionVersion(), approval.message(), user.username()).update();
        if (changed != 1) throw new ResponseStatusException(HttpStatus.CONFLICT,
                "이미 발송을 승인했거나 발송 결과를 확인해야 하는 메시지다");
        audit.record(incident.tenantId(), user.username(), "SLACK_SEND_APPROVED",
                Map.of("escalationId", escalationId, "channelLabel", preview.connection().channelLabel(),
                        "message", approval.message()), null);
    }

    public List<Delivery> list(long incidentId, CurrentUser user) {
        Incident incident = access(incidentId, user);
        return jdbc.sql("""
                SELECT d.* FROM slack_deliveries d JOIN escalations e ON e.id=d.escalation_id
                WHERE e.incident_id=? AND d.tenant_id=? ORDER BY d.approved_at
                """).params(incidentId, incident.tenantId())
                .query((r, n) -> new Delivery(r.getObject("id", UUID.class), r.getLong("escalation_id"),
                        r.getString("channel_label"), r.getString("message"), r.getString("status"),
                        r.getString("approved_by"), r.getInt("attempts"), r.getString("last_error"),
                        r.getObject("sent_at", OffsetDateTime.class))).list();
    }

    List<Claimed> claim() {
        // 프로세스가 중단되면 Slack 수신 여부가 불명확하므로 자동 재전송하지 않는다.
        jdbc.sql("UPDATE slack_deliveries SET status='UNKNOWN', last_error='전송 중단: Slack 수신 여부 확인 필요' "
                + "WHERE status='SENDING' AND claimed_at < now() - interval '2 minutes'").update();
        return jdbc.sql("""
                UPDATE slack_deliveries SET status='SENDING', attempts=attempts+1, claimed_at=now()
                WHERE id IN (SELECT id FROM slack_deliveries WHERE status='QUEUED' AND available_at<=now()
                             ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING id, tenant_id, message, connection_version, attempts
                """).query((r, n) -> new Claimed(r.getObject("id", UUID.class), r.getString("tenant_id"),
                        r.getString("message"), r.getLong("connection_version"), r.getInt("attempts"))).list();
    }

    void finish(Claimed claimed, SlackWebhookClient.Result result) {
        String status = result.status();
        if ("QUEUED".equals(status) && claimed.attempts() >= 5) status = "FAILED";
        int changed = jdbc.sql("""
                UPDATE slack_deliveries SET status=?, last_error=?, available_at=now()+make_interval(secs=>?),
                    sent_at=CASE WHEN ?='SENT' THEN now() ELSE sent_at END
                WHERE id=? AND attempts=? AND status IN ('SENDING', 'UNKNOWN')
                """).params(status, result.error(), result.retryAfterSeconds(), status, claimed.id(), claimed.attempts())
                .update();
        if (changed == 1) audit.record(claimed.tenantId(), "slack-relay", "SLACK_" + status,
                Map.of("deliveryId", claimed.id(), "attempts", claimed.attempts()), null);
    }
}

package com.opslens.backend.settings;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.config.OpsLensProperties;
import java.time.OffsetDateTime;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

/**
 * 테스트용 자동 승인. 관리자가 화면에서 켜고 끈다.
 * AUTO_APPROVE_ALLOWED=false인 환경(운영)에서는 켜져 있다고 저장돼 있어도 동작하지 않고, 켤 수도 없다.
 */
@Service
public class SettingsService {

    public record Settings(boolean autoApproveAllowed, boolean autoApprove, String updatedBy,
            OffsetDateTime updatedAt) {}

    private record Row(boolean enabled, String updatedBy, OffsetDateTime updatedAt) {}

    private static final String AUTO_APPROVE = "auto_approve";
    private static final Logger log = LoggerFactory.getLogger(SettingsService.class);

    private final JdbcClient jdbc;
    private final AuditLog audit;
    private final boolean autoApproveAllowed;

    public SettingsService(JdbcClient jdbc, AuditLog audit, OpsLensProperties properties) {
        this.jdbc = jdbc;
        this.audit = audit;
        this.autoApproveAllowed = properties.autoApproveAllowed();
        if (autoApproveAllowed) {
            log.warn("AUTO_APPROVE_ALLOWED=true: 관리자가 화면에서 자동 승인을 켤 수 있다 (테스트 전용)");
        }
    }

    public Settings get() {
        Row row = row();
        return new Settings(autoApproveAllowed, autoApproveAllowed && row != null && row.enabled(),
                row == null ? null : row.updatedBy(), row == null ? null : row.updatedAt());
    }

    public boolean autoApprove() {
        return get().autoApprove();
    }

    public Settings setAutoApprove(boolean enabled, CurrentUser user) {
        if (!user.isAdmin()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "MSP_ADMIN only");
        }
        if (!autoApproveAllowed) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "AUTO_APPROVE_ALLOWED=false인 환경이다");
        }
        jdbc.sql("""
                        INSERT INTO settings (name, enabled, updated_by) VALUES (?, ?, ?)
                        ON CONFLICT (name) DO UPDATE SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by,
                                                         updated_at = now()
                        """)
                .params(AUTO_APPROVE, enabled, user.username()).update();
        audit.record(null, user.username(), enabled ? "AUTO_APPROVE_ON" : "AUTO_APPROVE_OFF", Map.of(), null);
        return get();
    }

    private Row row() {
        return jdbc.sql("SELECT enabled, updated_by, updated_at FROM settings WHERE name = ?")
                .param(AUTO_APPROVE)
                .query((rs, rowNum) -> new Row(rs.getBoolean("enabled"), rs.getString("updated_by"),
                        rs.getObject("updated_at", OffsetDateTime.class)))
                .optional().orElse(null);
    }
}

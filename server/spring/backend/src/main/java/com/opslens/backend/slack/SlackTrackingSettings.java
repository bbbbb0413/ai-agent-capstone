package com.opslens.backend.slack;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class SlackTrackingSettings {
    public record Member(String slackUserId, String username) {}
    public record View(boolean configured, boolean enabled, String appId, String teamId, String channelId,
            long version, List<Member> members, List<String> eligibleUsers) {}
    public record Update(boolean enabled, String appId, String teamId, String channelId, String signingSecret,
            long version, List<Member> members) {}
    record Config(String tenant, boolean enabled, String appId, String teamId, String channelId, String encrypted, long version) {}
    private final JdbcClient jdbc;
    private final SlackSecretBox secrets;
    private final SlackConnectionService connections;
    private final AuditLog audit;
    public SlackTrackingSettings(JdbcClient jdbc, SlackSecretBox secrets, SlackConnectionService connections, AuditLog audit) {
        this.jdbc=jdbc; this.secrets=secrets; this.connections=connections; this.audit=audit;
    }
    private void admin(String tenant, CurrentUser user) {
        user.requireAccess(tenant);
        if (!user.isAdmin()) throw new ResponseStatusException(HttpStatus.FORBIDDEN, "관리자만 Slack 사용자 연결을 변경할 수 있다");
    }
    Optional<Config> find(String tenant) {
        return jdbc.sql("SELECT * FROM slack_tracking_settings WHERE tenant_id=?").param(tenant)
            .query((r,n)->new Config(tenant,r.getBoolean("enabled"),r.getString("app_id"),r.getString("team_id"),
                r.getString("channel_id"),r.getString("signing_secret_encrypted"),r.getLong("version"))).optional();
    }
    List<String> eligible(String tenant) {
        return jdbc.sql("""
            SELECT username FROM users u WHERE role='MSP_ADMIN' OR
            (role='MSP_ENGINEER' AND EXISTS (SELECT 1 FROM user_tenants t WHERE t.user_id=u.id AND t.tenant_id=?))
            ORDER BY username
            """).param(tenant).query(String.class).list();
    }
    public View view(String tenant, CurrentUser user) {
        admin(tenant,user);
        Config c=find(tenant).orElse(null);
        var members=jdbc.sql("SELECT slack_user_id, username FROM slack_members WHERE tenant_id=? ORDER BY username")
            .param(tenant).query((r,n)->new Member(r.getString(1),r.getString(2))).list();
        return c==null ? new View(false,false,"","","",0,members,eligible(tenant))
            :new View(true,c.enabled(),c.appId(),c.teamId(),c.channelId(),c.version(),members,eligible(tenant));
    }
    @Transactional
    public View save(String tenant, Update update, CurrentUser user) {
        admin(tenant,user); connections.lock(tenant);
        if (connections.find(tenant).isEmpty()) bad("먼저 Slack 발송 연결 정보를 저장한다");
        Config old=find(tenant).orElse(null);
        if ((old==null?0:old.version())!=update.version())
            throw new ResponseStatusException(HttpStatus.CONFLICT,"설정이 변경되었다. 새로고침한다");
        if (!matches(update.appId(),"A[A-Z0-9]{5,}") || !matches(update.teamId(),"T[A-Z0-9]{5,}")
                || !matches(update.channelId(),"[CG][A-Z0-9]{5,}")) bad("올바른 Slack 앱·워크스페이스·채널 ID를 입력한다");
        String encrypted=old==null?null:old.encrypted();
        if (update.signingSecret()!=null && !update.signingSecret().isBlank()) {
            if (!matches(update.signingSecret().trim(),"[a-fA-F0-9]{32}")) bad("Signing Secret 형식을 확인한다");
            encrypted=secrets.encrypt(tenant+":signing",update.signingSecret().trim());
        }
        if (encrypted==null) bad("Slack Signing Secret이 필요하다");
        if (update.members()==null || update.members().size()>100) bad("연결 사용자 목록은 최대 100명이다");
        var allowed=eligible(tenant); var ids=new HashSet<String>(); var names=new HashSet<String>();
        for (var member:update.members()) {
            if (member==null || !matches(member.slackUserId(),"[UW][A-Z0-9]{5,}") || !allowed.contains(member.username())
                    || !ids.add(member.slackUserId()) || !names.add(member.username())) bad("중복되지 않는 Slack 사용자와 해당 고객사의 MSP 계정을 선택한다");
        }
        if (update.enabled() && ids.isEmpty()) bad("버튼을 사용할 담당자를 한 명 이상 연결한다");
        jdbc.sql("""
            INSERT INTO slack_tracking_settings(tenant_id,app_id,team_id,channel_id,signing_secret_encrypted,enabled)
            VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET app_id=EXCLUDED.app_id,team_id=EXCLUDED.team_id,
              channel_id=EXCLUDED.channel_id,signing_secret_encrypted=EXCLUDED.signing_secret_encrypted,
              enabled=EXCLUDED.enabled,version=slack_tracking_settings.version+1
            """).params(tenant,update.appId(),update.teamId(),update.channelId(),encrypted,update.enabled()).update();
        jdbc.sql("DELETE FROM slack_members WHERE tenant_id=?").param(tenant).update();
        for (var member:update.members()) jdbc.sql("INSERT INTO slack_members VALUES(?,?,?)")
            .params(tenant,member.slackUserId(),member.username()).update();
        // 기존 메시지가 새 팀·채널·사용자 설정으로 처리되지 않게 연결 버전을 갱신한다.
        jdbc.sql("UPDATE slack_connections SET version=version+1 WHERE tenant_id=?").param(tenant).update();
        jdbc.sql("UPDATE slack_deliveries SET status='CANCELLED',last_error='Slack 처리 추적 설정 변경으로 취소됨' WHERE tenant_id=? AND status='QUEUED'")
            .param(tenant).update();
        audit.record(tenant,user.username(),"SLACK_TRACKING_SETTINGS_UPDATED",Map.of("enabled",update.enabled(),"members",ids.size()),null);
        return view(tenant,user);
    }
    String secret(Config config) { return secrets.decrypt(config.tenant()+":signing",config.encrypted()); }
    CurrentUser actor(String tenant,String slackUser) {
        return jdbc.sql("""
            SELECT u.id,u.username,u.role FROM slack_members m JOIN users u ON u.username=m.username
            WHERE m.tenant_id=? AND m.slack_user_id=? AND (u.role='MSP_ADMIN' OR
              (u.role='MSP_ENGINEER' AND EXISTS(SELECT 1 FROM user_tenants t WHERE t.user_id=u.id AND t.tenant_id=?)))
            """).params(tenant,slackUser,tenant)
            .query((r,n)->new CurrentUser(r.getLong(1),r.getString(2),r.getString(3),List.of(tenant))).optional().orElse(null);
    }
    static boolean matches(String value,String pattern) { return value!=null && value.matches(pattern); }
    static void bad(String message) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST,message); }
}

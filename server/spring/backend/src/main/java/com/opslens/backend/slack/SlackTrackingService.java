package com.opslens.backend.slack;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.*;
import java.util.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class SlackTrackingService {
    public record Event(String action,String actor,String source,OffsetDateTime createdAt) {}
    public record Tracking(UUID deliveryId,String status,String acknowledgedBy,OffsetDateTime acknowledgedAt,
            String assignedTo,OffsetDateTime assignedAt,String completedBy,OffsetDateTime completedAt,List<Event> events) {}
    record Row(UUID id,String tenant,long incident,long connectionVersion,boolean interactive,String deliveryStatus,
            String message,String status,String acknowledgedBy,OffsetDateTime acknowledgedAt,String assignedTo,
            OffsetDateTime assignedAt,String completedBy,OffsetDateTime completedAt,String messageTs) {}
    private final JdbcClient jdbc;
    private final SlackTrackingSettings settings;
    private final SlackConnectionService connections;
    private final SlackSecretBox secrets;
    private final ObjectMapper json;
    private final AuditLog audit;
    public SlackTrackingService(JdbcClient jdbc,SlackTrackingSettings settings,SlackConnectionService connections,
            SlackSecretBox secrets,ObjectMapper json,AuditLog audit) {
        this.jdbc=jdbc;this.settings=settings;this.connections=connections;this.secrets=secrets;this.json=json;this.audit=audit;
    }
    private static final String SELECT="SELECT d.*,e.incident_id FROM slack_deliveries d JOIN escalations e ON e.id=d.escalation_id ";
    private Row row(UUID id,boolean lock) {
        return jdbc.sql(SELECT+"WHERE d.id=?"+(lock?" FOR UPDATE OF d":"")).param(id).query((r,n)->new Row(
            id,r.getString("tenant_id"),r.getLong("incident_id"),r.getLong("connection_version"),r.getBoolean("tracking_enabled"),
            r.getString("status"),r.getString("message"),r.getString("handling_status"),r.getString("acknowledged_by"),
            r.getObject("acknowledged_at",OffsetDateTime.class),r.getString("assigned_to"),r.getObject("assigned_at",OffsetDateTime.class),
            r.getString("completed_by"),r.getObject("completed_at",OffsetDateTime.class),r.getString("slack_message_ts")))
            .optional().orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"알림을 찾을 수 없다"));
    }
    private Tracking tracking(Row r) {
        var events=jdbc.sql("SELECT action,actor,source,created_at FROM slack_tracking_events WHERE delivery_id=? ORDER BY id")
            .param(r.id()).query((v,n)->new Event(v.getString(1),v.getString(2),v.getString(3),v.getObject(4,OffsetDateTime.class))).list();
        return new Tracking(r.id(),r.status(),r.acknowledgedBy(),r.acknowledgedAt(),r.assignedTo(),r.assignedAt(),r.completedBy(),r.completedAt(),events);
    }
    public List<Tracking> list(long incident,CurrentUser user) {
        user.requireMspStaff();
        String tenant=jdbc.sql("SELECT tenant_id FROM incidents WHERE id=?").param(incident).query(String.class).optional()
            .orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"장애를 찾을 수 없다"));
        user.requireAccess(tenant);
        return jdbc.sql("SELECT d.id FROM slack_deliveries d JOIN escalations e ON e.id=d.escalation_id WHERE e.incident_id=? AND d.tenant_id=?")
            .params(incident,tenant).query(UUID.class).list().stream().map(id->tracking(row(id,false))).toList();
    }
    @Transactional
    public void act(long incident,UUID id,String action,CurrentUser user) {
        user.requireMspStaff(); Row initial=row(id,false); user.requireAccess(initial.tenant());
        if (initial.incident()!=incident) throw new ResponseStatusException(HttpStatus.NOT_FOUND,"장애에 해당하는 알림이 아니다");
        Row r=row(id,true);
        if (!"SENT".equals(r.deliveryStatus())) throw new ResponseStatusException(HttpStatus.CONFLICT,"발송 완료한 알림만 처리할 수 있다");
        String error=transition(r,action,user,"WEB");
        if (error!=null) throw new ResponseStatusException(HttpStatus.CONFLICT,error);
    }
    /** 네트워크 응답을 기다리지 않고 DB에 결과와 원문 갱신 요청만 기록한다. */
    @Transactional
    public void interact(byte[] raw,String timestamp,String signature) {
        if (raw.length>65536) throw new ResponseStatusException(HttpStatus.PAYLOAD_TOO_LARGE);
        JsonNode payload;
        UUID id;
        try {
            String encoded=null;
            for (String part:new String(raw,StandardCharsets.UTF_8).split("&")) {
                String[] pair=part.split("=",2);
                if ("payload".equals(pair[0]) && pair.length==2) {
                    if (encoded!=null) throw new IllegalArgumentException();
                    encoded=pair[1];
                }
            }
            payload=json.readTree(URLDecoder.decode(Objects.requireNonNull(encoded),StandardCharsets.UTF_8));
            if (!"block_actions".equals(payload.path("type").asText()) || payload.path("actions").size()!=1) throw new IllegalArgumentException();
            id=UUID.fromString(payload.path("actions").get(0).path("value").asText());
        } catch (Exception e) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"잘못된 Slack 요청"); }
        jdbc.sql("SET LOCAL lock_timeout='750ms'").update();
        jdbc.sql("SET LOCAL statement_timeout='1500ms'").update();
        Row initial=row(id,false);
        var config=settings.find(initial.tenant()).orElseThrow(()->new ResponseStatusException(HttpStatus.UNAUTHORIZED));
        if (!SlackSignature.valid(settings.secret(config),timestamp,signature,raw,Instant.now()))
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED,"Slack 서명을 확인할 수 없다");
        connections.lock(initial.tenant());
        // 설정 변경과 같은 잠금을 사용하고 잠금 후 다시 읽는다.
        config=settings.find(initial.tenant()).orElseThrow();
        var connection=connections.find(initial.tenant()).orElseThrow();
        if (!config.enabled() || !connection.enabled() || !initial.interactive()
                || initial.connectionVersion()!=connection.version()
                || !config.appId().equals(payload.path("api_app_id").asText())
                || !config.teamId().equals(payload.path("team").path("id").asText())
                || !config.channelId().equals(payload.path("channel").path("id").asText()))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,"Slack 연결 범위가 다르거나 설정이 변경되었다");
        String responseUrl=payload.path("response_url").asText();
        validateResponseUrl(responseUrl);
        String action=payload.path("actions").get(0).path("action_id").asText();
        if (!List.of("opslens_ack","opslens_start","opslens_complete").contains(action))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"지원하지 않는 버튼");
        String messageTs=payload.path("container").path("message_ts").asText();
        if (!messageTs.matches("[0-9]+\\.[0-9]+") || !"message".equals(payload.path("container").path("type").asText()))
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"Slack 메시지 정보가 없다");
        String digest;
        try { digest=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)); }
        catch (Exception e) { throw new IllegalStateException(e); }
        if (jdbc.sql("INSERT INTO slack_interaction_receipts(digest) VALUES(?) ON CONFLICT DO NOTHING").param(digest).update()==0) return;
        Row r=row(id,true);
        if (r.messageTs()!=null && !r.messageTs().equals(messageTs)) throw new ResponseStatusException(HttpStatus.FORBIDDEN,"다른 Slack 메시지다");
        if (!List.of("SENT","SENDING","UNKNOWN").contains(r.deliveryStatus()))
            throw new ResponseStatusException(HttpStatus.CONFLICT,"발송한 알림이 아니다");
        CurrentUser actor=settings.actor(r.tenant(),payload.path("user").path("id").asText());
        String error=actor==null?"OpsLens 관리자가 Slack 사용자 ID와 담당 계정을 연결해야 합니다.":transition(r,action.substring(8).toUpperCase(Locale.ROOT),actor,"SLACK");
        if (actor!=null && error==null) jdbc.sql("UPDATE slack_deliveries SET slack_message_ts=?,status='SENT',sent_at=COALESCE(sent_at,now()),last_error=NULL WHERE id=?")
            .params(messageTs,id).update();
        enqueue(r.id(),responseUrl,error);
    }
    private String transition(Row r,String action,CurrentUser user,String source) {
        if (action==null || !List.of("ACK","START","COMPLETE").contains(action)) return "지원하지 않는 처리 단계입니다.";
        if ("ACK".equals(action)) {
            if (!"WAITING".equals(r.status())) return "이미 확인된 알림입니다.";
            jdbc.sql("UPDATE slack_deliveries SET handling_status='ACKNOWLEDGED',acknowledged_by=?,acknowledged_at=now() WHERE id=?")
                .params(user.username(),r.id()).update();
        } else if ("START".equals(action)) {
            if (!List.of("WAITING","ACKNOWLEDGED").contains(r.status())) return "이미 담당자가 처리 중이거나 완료한 알림입니다.";
            jdbc.sql("UPDATE slack_deliveries SET handling_status='IN_PROGRESS',acknowledged_by=COALESCE(acknowledged_by,?),acknowledged_at=COALESCE(acknowledged_at,now()),assigned_to=?,assigned_at=now() WHERE id=?")
                .params(user.username(),user.username(),r.id()).update();
        } else {
            if (!"IN_PROGRESS".equals(r.status())) return "담당 시작 후 처리 완료할 수 있습니다.";
            if (!user.isAdmin() && !user.username().equals(r.assignedTo())) return "배정된 담당자 또는 관리자만 완료할 수 있습니다.";
            jdbc.sql("UPDATE slack_deliveries SET handling_status='COMPLETED',completed_by=?,completed_at=now() WHERE id=?")
                .params(user.username(),r.id()).update();
        }
        jdbc.sql("INSERT INTO slack_tracking_events(delivery_id,action,actor,source) VALUES(?,?,?,?)")
            .params(r.id(),action,user.username(),source).update();
        audit.record(r.tenant(),user.username(),"SLACK_TRACKING_"+action,Map.of("deliveryId",r.id(),"source",source),null);
        return null;
    }
    void enqueue(UUID id,String url,String feedback) {
        jdbc.sql("INSERT INTO slack_tracking_updates(delivery_id,url_encrypted,feedback) VALUES(?,?,?)")
            .params(id,secrets.encrypt(id+":response",url),feedback).update();
    }
    static void validateResponseUrl(String url) {
        try {
            URI uri=URI.create(url);
            if ("https".equals(uri.getScheme()) && "hooks.slack.com".equals(uri.getHost()) && uri.getPort()==-1
                    && uri.getUserInfo()==null && uri.getQuery()==null && uri.getFragment()==null
                    && uri.getPath().matches("/(actions|commands)/[A-Za-z0-9/_-]+")) return;
        } catch (IllegalArgumentException ignored) {}
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"잘못된 Slack 응답 주소");
    }
    Map<String,Object> updatePayload(UUID id,String feedback) {
        if (feedback!=null) return Map.of("response_type","ephemeral","replace_original",false,"text",feedback);
        Row r=row(id,false);
        var result=new HashMap<>(payload(r.message(),id,r.status(),r.acknowledgedBy(),r.assignedTo()));
        result.put("replace_original",true);
        return result;
    }
    static Map<String,Object> payload(String message,UUID id,String status,String acknowledgedBy,String assignedTo) {
        var blocks=new ArrayList<Map<String,Object>>();
        blocks.add(Map.of("type","section","text",Map.of("type","plain_text","text",message,"emoji",false)));
        String label=switch(status) { case "ACKNOWLEDGED"->"확인됨";case "IN_PROGRESS"->"담당 처리 중";case "COMPLETED"->"처리 완료";default->"미확인"; };
        String detail="처리 상태: "+label+(acknowledgedBy==null?"":" · 확인: "+acknowledgedBy)+(assignedTo==null?"":" · 담당: "+assignedTo);
        blocks.add(Map.of("type","context","elements",List.of(Map.of("type","plain_text","text",detail))));
        if (!"COMPLETED".equals(status)) {
            var buttons=new ArrayList<Map<String,Object>>();
            if ("WAITING".equals(status)) buttons.add(button("확인했습니다","ack",id));
            if (List.of("WAITING","ACKNOWLEDGED").contains(status)) buttons.add(button("담당 시작","start",id));
            if ("IN_PROGRESS".equals(status)) buttons.add(button("처리 완료","complete",id));
            blocks.add(Map.of("type","actions","block_id","opslens_tracking","elements",buttons));
        }
        blocks.add(Map.of("type","context","elements",List.of(Map.of("type","plain_text","text","담당자 처리 기록입니다. 실제 장애 복구 상태와는 별개입니다."))));
        return Map.of("text",message.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"),"blocks",blocks);
    }
    private static Map<String,Object> button(String label,String action,UUID id) {
        return Map.of("type","button","text",Map.of("type","plain_text","text",label),"action_id","opslens_"+action,"value",id.toString());
    }
}

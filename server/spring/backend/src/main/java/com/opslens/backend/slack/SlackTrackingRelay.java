package com.opslens.backend.slack;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class SlackTrackingRelay {
    record Update(long id,UUID delivery,String encrypted,int attempts) {}
    private final JdbcClient jdbc;
    private final SlackSecretBox secrets;
    private final SlackTrackingService tracking;
    private final ObjectMapper json;
    private final HttpClient http;
    private final java.util.concurrent.ExecutorService executor=Executors.newSingleThreadExecutor(Thread.ofVirtual().factory());
    private final AtomicBoolean running=new AtomicBoolean();
    @org.springframework.beans.factory.annotation.Autowired
    public SlackTrackingRelay(JdbcClient jdbc,SlackSecretBox secrets,SlackTrackingService tracking,ObjectMapper json) {
        this(jdbc,secrets,tracking,json,HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
            .followRedirects(HttpClient.Redirect.NEVER).build());
    }
    SlackTrackingRelay(JdbcClient jdbc,SlackSecretBox secrets,SlackTrackingService tracking,ObjectMapper json,HttpClient http) {
        this.jdbc=jdbc;this.secrets=secrets;this.tracking=tracking;this.json=json;this.http=http;
    }
    @Scheduled(fixedDelay=1000,initialDelay=3000)
    void poll() {
        if (!running.compareAndSet(false,true)) return;
        executor.execute(()->{try { sendPending(); } finally { running.set(false); }});
    }
    @PreDestroy void stop() { executor.shutdownNow(); }
    void sendPending() {
        // replace_original로 기존 메시지만 갱신한다. 처리 상태의 원본은 OpsLens DB다.
        jdbc.sql("UPDATE slack_tracking_updates SET status='FAILED',last_error='Slack 표시 갱신 시간이 만료됨' WHERE status IN ('QUEUED','SENDING') AND expires_at<=now()") .update();
        jdbc.sql("UPDATE slack_tracking_updates SET status='QUEUED' WHERE status='SENDING' AND available_at<now()-interval '1 minute' AND attempts<3").update();
        jdbc.sql("UPDATE slack_tracking_updates SET status='FAILED',last_error='Slack 표시 갱신 결과 확인 필요' WHERE status='SENDING' AND available_at<now()-interval '1 minute' AND attempts>=3").update();
        var next=jdbc.sql("""
            UPDATE slack_tracking_updates SET status='SENDING',attempts=attempts+1,available_at=now()
            WHERE id IN(SELECT u.id FROM slack_tracking_updates u WHERE u.status='QUEUED' AND u.available_at<=now()
              AND NOT EXISTS(SELECT 1 FROM slack_tracking_updates p WHERE p.delivery_id=u.delivery_id AND p.id<u.id AND p.status IN ('QUEUED','SENDING'))
              ORDER BY u.id FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id,delivery_id,url_encrypted,attempts
            """).query((r,n)->new Update(r.getLong(1),r.getObject(2,UUID.class),r.getString(3),r.getInt(4))).optional();
        if (next.isEmpty()) return;
        Update update=next.get();
        String status="FAILED",error="Slack 표시를 갱신하지 못했습니다. OpsLens 처리 기록을 확인하세요.";
        int delay=5;
        try {
            String url=secrets.decrypt(update.delivery()+":response",update.encrypted());
            SlackTrackingService.validateResponseUrl(url);
            String feedback=(String)jdbc.sql("SELECT feedback FROM slack_tracking_updates WHERE id=?").param(update.id()).query().singleRow().get("feedback");
            var request=HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(5)).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(tracking.updatePayload(update.delivery(),feedback)))).build();
            var response=http.send(request,HttpResponse.BodyHandlers.ofString());
            if (response.statusCode()==200 && ("ok".equals(response.body().trim()) || json.readTree(response.body()).path("ok").asBoolean(false))) {
                status="SENT";error=null;
            } else if (response.statusCode()==429 && update.attempts()<3) {
                status="QUEUED";
                try { delay=Math.max(1,Math.min(1200,Integer.parseInt(response.headers().firstValue("Retry-After").orElse("5")))); }
                catch(NumberFormatException ignored) {}
            }
        } catch(InterruptedException e) { Thread.currentThread().interrupt(); }
        catch(Exception ignored) { /* 응답 주소나 토큰이 로그에 남지 않게 한다. */ }
        jdbc.sql("UPDATE slack_tracking_updates SET status=?,last_error=?,available_at=now()+make_interval(secs=>?) WHERE id=?")
            .params(status,error,delay,update.id()).update();
    }
}

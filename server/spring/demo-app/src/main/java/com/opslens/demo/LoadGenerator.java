package com.opslens.demo;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Profile;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * 부하 발생 모드(DEMO_MODE=loadgen). 고객사 서비스의 POST /orders에 초당 rate건을 보낸다 (트래픽 급증 장애).
 * 응답을 기다리지 않고 일정한 속도로 보낸다(open loop). 서비스가 느려져도 보내는 속도가 줄지 않아야 실제 급증과 같다.
 * 응답을 기다리는 요청이 max-in-flight를 넘으면 그 순간의 요청은 건너뛴다(사용자가 포기한 것으로 본다).
 * 상한이 없으면 처리 용량을 넘는 동안 서버의 대기 요청이 끝없이 늘어 JVM 힙이 터졌다.
 * HttpClient는 1초마다 새로 만든다. 한 클라이언트가 처음 맺은 연결만 계속 쓰면 레플리카를 늘려도 새 파드에 요청이
 * 가지 않았다(Service는 연결 단위로 나눈다). 실제 사용자처럼 새 연결이 계속 들어와야 확장 효과가 보인다.
 */
@Component
@Profile("loadgen")
public class LoadGenerator {

    private static final Logger log = LoggerFactory.getLogger(LoadGenerator.class);
    private static final int TICK_MS = 10;

    private volatile HttpClient client = newClient();
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
    private final AtomicInteger inFlight = new AtomicInteger();
    private final AtomicInteger sent = new AtomicInteger();
    private final AtomicInteger ok = new AtomicInteger();
    private final AtomicInteger failed = new AtomicInteger();
    private final AtomicInteger skipped = new AtomicInteger();
    private final HttpRequest request;
    private final double perTick;
    private final int maxInFlight;
    private double carry;

    public LoadGenerator(@Value("${demo.loadgen.target}") String target, @Value("${demo.loadgen.rate}") int rate,
            @Value("${demo.loadgen.max-in-flight}") int maxInFlight) {
        this.request = HttpRequest.newBuilder(URI.create(target)).timeout(Duration.ofSeconds(30))
                .POST(HttpRequest.BodyPublishers.noBody()).build();
        this.perTick = rate * TICK_MS / 1000.0;
        this.maxInFlight = maxInFlight;
        log.info("loadgen: {} req/s → {}", rate, target);
    }

    @EventListener(ApplicationReadyEvent.class)
    void start() {
        scheduler.scheduleAtFixedRate(this::tick, 0, TICK_MS, TimeUnit.MILLISECONDS);
        scheduler.scheduleAtFixedRate(this::report, 10, 10, TimeUnit.SECONDS);
        scheduler.scheduleAtFixedRate(this::renewClient, 1, 1, TimeUnit.SECONDS);
    }

    private void tick() {
        carry += perTick;
        for (; carry >= 1; carry--) {
            if (inFlight.get() >= maxInFlight) {
                skipped.incrementAndGet();
                continue;
            }
            inFlight.incrementAndGet();
            sent.incrementAndGet();
            client.sendAsync(request, HttpResponse.BodyHandlers.discarding()).whenComplete((response, error) -> {
                inFlight.decrementAndGet();
                (error == null && response.statusCode() == 200 ? ok : failed).incrementAndGet();
            });
        }
    }

    /**
     * 새 클라이언트로 바꾸고, 이전 클라이언트는 진행 중인 요청이 끝나면 닫는다(close는 그때까지 기다린다).
     * 닫지 않으면 HttpClient가 유휴 연결을 기본 20분 동안 쥐고 있어, 1초마다 새 연결이 서버에 쌓여 파드가 OOMKilled됐다.
     */
    private void renewClient() {
        HttpClient previous = client;
        client = newClient();
        Thread.ofVirtual().start(previous::close);
    }

    private static HttpClient newClient() {
        return HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    private void report() {
        log.info("loadgen last 10s: sent={}, ok={}, failed={}, skipped={}, inFlight={}",
                sent.getAndSet(0), ok.getAndSet(0), failed.getAndSet(0), skipped.getAndSet(0), inFlight.get());
    }
}

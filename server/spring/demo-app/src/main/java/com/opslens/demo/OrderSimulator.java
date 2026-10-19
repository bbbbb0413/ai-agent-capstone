package com.opslens.demo;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.concurrent.ThreadLocalRandom;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 주문 요청을 스스로 만들어 메트릭(demo_orders_seconds{outcome})과 로그를 쌓는다.
 * 실패 로그에는 일부러 고객 이메일·전화번호를 넣는다. Agent Harness의 마스킹을 시험하는 용도다.
 */
@Component
public class OrderSimulator {

    private static final Logger log = LoggerFactory.getLogger(OrderSimulator.class);
    private static final Logger payment = LoggerFactory.getLogger("PaymentGatewayClient");
    private static final Logger hikari = LoggerFactory.getLogger("com.zaxxer.hikari.pool.HikariPool");

    private final FaultState faults;
    private final MeterRegistry registry;

    public OrderSimulator(FaultState faults, MeterRegistry registry) {
        this.faults = faults;
        this.registry = registry;
    }

    private final java.util.concurrent.atomic.AtomicInteger processed = new java.util.concurrent.atomic.AtomicInteger();
    private final java.util.concurrent.atomic.AtomicInteger failed = new java.util.concurrent.atomic.AtomicInteger();

    @Scheduled(fixedRateString = "${demo.traffic-interval-ms}")
    void placeOrder() {
        long started = System.nanoTime();
        boolean ok = handle();
        processed.incrementAndGet();
        if (!ok) {
            failed.incrementAndGet();
        }
        String outcome = ok ? "success" : "error";
        Timer.builder("demo.orders")
                .description("주문 처리 시간")
                .tag("outcome", outcome)
                .register(registry)
                .record(System.nanoTime() - started, java.util.concurrent.TimeUnit.NANOSECONDS);
    }

    /** 정상 상태에서도 로그가 쌓이도록 10초마다 처리 현황을 남긴다. */
    @Scheduled(fixedRate = 10_000, initialDelay = 10_000)
    void logSummary() {
        log.info("orders processed in last 10s: total={}, failed={}, fault={}, poolSize={}",
                processed.getAndSet(0), failed.getAndSet(0), faults.fault(), faults.poolSize());
    }

    private boolean handle() {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        sleep(random.nextLong(20, 80));
        switch (faults.fault()) {
            case DB_POOL_EXHAUSTION -> {
                if (random.nextBoolean()) {
                    hikari.error("HikariPool-1 - Connection is not available, request timed out after 30000ms "
                            + "(total={}, active={}, idle=0, waiting={})",
                            faults.poolSize(), faults.poolSize(), random.nextInt(15, 40));
                    log.error("order failed: 503 Service Unavailable customer=minsu.kim@example.com phone=010-1234-5678");
                    return false;
                }
            }
            case PAYMENT_LATENCY -> {
                long delay = random.nextLong(1500, 2200);
                sleep(delay);
                payment.warn("payment API timeout after {}ms, retrying (gateway=pg.example.com)", delay);
            }
            case WORKER_HANG -> {
                log.error("order rejected: 503 worker pool saturated (active=200/200, queued=1000/1000), "
                        + "oldest worker blocked {}s waiting for internal lock ReservationCache.refreshLock (deadlock suspected)", random.nextInt(120, 600));
                return false;
            }
            case NONE -> {
            }
        }
        return true;
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}

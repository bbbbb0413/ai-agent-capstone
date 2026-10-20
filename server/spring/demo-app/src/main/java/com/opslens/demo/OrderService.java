package com.opslens.demo;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.util.concurrent.Semaphore;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;

/**
 * 주문 하나를 처리한다. 자체 생성 트래픽(OrderSimulator)과 외부 요청(POST /orders)이 같은 경로를 쓴다.
 *
 * 처리 용량 모델 (SPEC-fault-based-actions 5.6):
 *   1. 처리 슬롯(demo.workers, 기본 4)을 잡고 앱 내부 처리를 한다. 50ms 안팎이라 파드당 용량은 초당 약 80건이다.
 *      그중 약 5ms는 실제로 CPU를 쓴다. sleep만 하면 과부하에도 CPU가 오르지 않아 HPA가 반응하지 않는다(결정 8).
 *      슬롯이 모자라면 기다린다. 이 대기가 트래픽 급증의 증상(`request queue backlog`, p95 상승)이다. 에러는 내지 않는다.
 *   2. 슬롯을 놓고 의존 대상(DB, 결제 API)을 호출한다. 비동기 I/O로 보고 슬롯을 점유하지 않는다.
 *      그래서 의존 대상이 느려도 대기열 경고는 나오지 않고, 그 대상의 지연 로그와 메트릭만 나온다(장애 형태 구분).
 * 메트릭 demo_orders_seconds는 슬롯 대기를 포함한 전체 시간, demo_dependency_seconds{dependency}는 의존 호출 시간이다.
 * 실패 로그에는 일부러 고객 이메일·전화번호를 넣는다. Agent Harness의 마스킹을 시험하는 용도다.
 */
@Service
@Profile("!loadgen")
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);
    private static final Logger payment = LoggerFactory.getLogger("PaymentGatewayClient");
    private static final Logger hikari = LoggerFactory.getLogger("com.zaxxer.hikari.pool.HikariPool");
    private static final Logger jdbc = LoggerFactory.getLogger("org.hibernate.SQL_SLOW");
    private static final long BACKLOG_WARN_MS = 200;
    private static final long CPU_MILLIS = 5;

    private final FaultState faults;
    private final MeterRegistry registry;
    private final int workers;
    private final Semaphore slots;
    private final AtomicLong lastBacklogWarn = new AtomicLong();
    private final AtomicInteger processed = new AtomicInteger();
    private final AtomicInteger failed = new AtomicInteger();

    public OrderService(FaultState faults, MeterRegistry registry, @Value("${demo.workers}") int workers) {
        this.faults = faults;
        this.registry = registry;
        this.workers = workers;
        this.slots = new Semaphore(workers, true);
    }

    /** 처리 결과(성공 여부)를 메트릭에 남기고 돌려준다. */
    public boolean process() {
        return process(System.nanoTime());
    }

    /** received: 요청을 받은 시각(System.nanoTime). 처리 스레드로 넘어가기 전의 대기도 응답 시간에 넣는다. */
    public boolean process(long received) {
        long started = received;
        boolean ok = handle();
        processed.incrementAndGet();
        if (!ok) {
            failed.incrementAndGet();
        }
        Timer.builder("demo.orders")
                .description("주문 처리 시간 (처리 슬롯 대기 포함)")
                .tag("outcome", ok ? "success" : "error")
                .register(registry)
                .record(System.nanoTime() - started, TimeUnit.NANOSECONDS);
        return ok;
    }

    /** 최근 집계를 돌려주고 0으로 되돌린다: [처리, 실패] */
    int[] drainCounts() {
        return new int[] {processed.getAndSet(0), failed.getAndSet(0)};
    }

    private boolean handle() {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        if (!acquireSlot()) {
            return false;
        }
        try {
            long cpu = busy(CPU_MILLIS);
            sleep(Math.max(0, random.nextLong(20, 80) - cpu));
            switch (faults.fault()) {
                case DB_POOL_EXHAUSTION -> {
                    if (random.nextBoolean()) {
                        hikari.error("HikariPool-1 - Connection is not available, request timed out after 30000ms "
                                + "(total={}, active={}, idle=0, waiting={})",
                                faults.poolSize(), faults.poolSize(), random.nextInt(15, 40));
                        log.error("order failed: 503 Service Unavailable customer=minsu.kim@example.com "
                                + "phone=010-1234-5678");
                        return false;
                    }
                }
                case WORKER_HANG -> {
                    log.error("order rejected: 503 worker pool saturated (active=200/200, queued=1000/1000), "
                            + "oldest worker blocked {}s waiting for internal lock ReservationCache.refreshLock "
                            + "(deadlock suspected)", random.nextInt(120, 600));
                    return false;
                }
                default -> {
                }
            }
        } finally {
            slots.release();
        }
        // 의존 대상 호출 (슬롯 밖)
        long query = faults.fault() == FaultState.Fault.DB_SLOW ? random.nextLong(800, 1500) : random.nextLong(2, 10);
        dependency("postgres", query);
        if (query >= 500) {
            jdbc.warn("slow query: SELECT * FROM orders WHERE customer_id = ? FOR UPDATE took {}ms (db=postgres)",
                    query);
        }
        long call = faults.fault() == FaultState.Fault.PAYMENT_LATENCY
                ? random.nextLong(1500, 2200) : random.nextLong(20, 60);
        dependency("payment-gateway", call);
        if (call >= 1000) {
            payment.warn("payment API timeout after {}ms, retrying (gateway=pg.example.com)", call);
        }
        return true;
    }

    private boolean acquireSlot() {
        long waitStarted = System.nanoTime();
        try {
            slots.acquire();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        long waitedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - waitStarted);
        long now = System.currentTimeMillis();
        long last = lastBacklogWarn.get();
        // 과부하에서 요청마다 남기면 로그가 넘친다. 초당 한 번만 남긴다
        if (waitedMs > BACKLOG_WARN_MS && now - last >= 1_000 && lastBacklogWarn.compareAndSet(last, now)) {
            // 파드 멈춤의 `503 worker pool saturated`와 헷갈리지 않게 "worker"라는 말을 쓰지 않는다
            log.warn("request queue backlog: {} requests waiting for a processing slot ({}/{} slots busy), "
                    + "queue wait {}ms", slots.getQueueLength(), workers - slots.availablePermits(), workers, waitedMs);
        }
        return true;
    }

    private void dependency(String name, long millis) {
        sleep(millis);
        Timer.builder("demo.dependency")
                .description("의존 대상 호출 시간")
                .tag("dependency", name)
                .register(registry)
                .record(millis, TimeUnit.MILLISECONDS);
    }

    /** millis 동안 CPU를 쓴다(주문 검증·가격 계산 흉내). 실제로 쓴 시간을 ms로 돌려준다. */
    private static long busy(long millis) {
        long started = System.nanoTime();
        long deadline = started + TimeUnit.MILLISECONDS.toNanos(millis);
        long x = 0;
        while (System.nanoTime() < deadline) {
            x += Long.numberOfTrailingZeros(x * 31 + 7);
        }
        if (x == 42) {
            log.trace("unreachable");   // 계산을 JIT가 없애지 않게 결과를 쓴다
        }
        return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started);
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}

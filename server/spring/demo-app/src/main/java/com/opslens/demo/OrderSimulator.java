package com.opslens.demo;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** 평상시 트래픽(초당 약 5건)을 스스로 만들고, 정상 상태에서도 로그가 쌓이도록 10초마다 처리 현황을 남긴다. */
@Component
@Profile("!loadgen")
public class OrderSimulator {

    private static final Logger log = LoggerFactory.getLogger(OrderSimulator.class);

    private final OrderService orders;
    private final FaultState faults;

    public OrderSimulator(OrderService orders, FaultState faults) {
        this.orders = orders;
        this.faults = faults;
    }

    @Scheduled(fixedRateString = "${demo.traffic-interval-ms}")
    void placeOrder() {
        orders.process();
    }

    @Scheduled(fixedRate = 10_000, initialDelay = 10_000)
    void logSummary() {
        int[] counts = orders.drainCounts();
        log.info("orders processed in last 10s: total={}, failed={}, fault={}, poolSize={}",
                counts[0], counts[1], faults.faults(), faults.poolSize());
    }
}

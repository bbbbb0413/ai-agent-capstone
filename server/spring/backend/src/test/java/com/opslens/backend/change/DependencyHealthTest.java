package com.opslens.backend.change;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/** 의존 대상 상태 판정 기준. 데모 앱 기준값: DB 2~10ms, 결제 20~60ms / DB_SLOW 0.8~1.5초, PAYMENT_LATENCY 1.5~2.2초 */
class DependencyHealthTest {

    @Test
    void latencyDecidesStatusAndNoCallsMeansUnknown() {
        assertEquals("HEALTHY", DependencyHealth.status(0.009, 5.0));
        assertEquals("DEGRADED", DependencyHealth.status(1.4, 5.0));    // DB_SLOW, PAYMENT_LATENCY
        assertEquals("DOWN", DependencyHealth.status(3.5, 5.0));
        assertEquals("UNKNOWN", DependencyHealth.status(null, 5.0));
        assertEquals("UNKNOWN", DependencyHealth.status(0.01, 0.0));
    }
}

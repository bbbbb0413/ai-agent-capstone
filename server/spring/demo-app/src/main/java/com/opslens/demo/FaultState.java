package com.opslens.demo;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.stereotype.Component;

/**
 * 주입된 장애 상태.
 *   DB_POOL_EXHAUSTION : 시나리오 A. 요청 절반이 커넥션 획득 실패로 5xx
 *   PAYMENT_LATENCY    : 시나리오 B. 외부 결제 API가 1.5초 이상 지연
 */
@Component
public class FaultState {

    public enum Fault { NONE, DB_POOL_EXHAUSTION, PAYMENT_LATENCY }

    /** 이 크기 이상이면 커넥션 풀 고갈이 해소된다 (런북의 권장 조치와 맞춘다) */
    public static final int RECOVERY_POOL_SIZE = 20;

    private final AtomicReference<Fault> fault = new AtomicReference<>(Fault.NONE);
    private final AtomicInteger poolSize = new AtomicInteger(10);

    public Fault fault() {
        return fault.get();
    }

    public int poolSize() {
        return poolSize.get();
    }

    public void inject(Fault value) {
        fault.set(value);
        if (value == Fault.DB_POOL_EXHAUSTION) {
            poolSize.set(10);
        }
    }

    public void clear() {
        fault.set(Fault.NONE);
    }

    public void resizePool(int size) {
        poolSize.set(size);
        if (size >= RECOVERY_POOL_SIZE && fault.get() == Fault.DB_POOL_EXHAUSTION) {
            fault.set(Fault.NONE);
        }
    }
}

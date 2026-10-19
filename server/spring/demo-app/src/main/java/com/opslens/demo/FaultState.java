package com.opslens.demo;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 주입된 장애 상태.
 *   DB_POOL_EXHAUSTION : 시나리오 A. 요청 절반이 커넥션 획득 실패로 5xx
 *   PAYMENT_LATENCY    : 시나리오 B2. 외부 결제 API가 1.5초 이상 지연
 *
 * 시작할 때 설정(DEMO_POOL_SIZE)으로 풀 크기를 받는다. 20보다 작으면 처음부터 커넥션 풀 고갈 상태다.
 * 그래서 GitOps 고객사의 "잘못된 설정 배포"는 매니페스트의 이 값을 줄이는 커밋으로 만들고,
 * 롤백은 그 커밋을 되돌리는 PR로 한다.
 */
@Component
public class FaultState {

    public enum Fault { NONE, DB_POOL_EXHAUSTION, PAYMENT_LATENCY }

    /** 이 크기 이상이면 커넥션 풀 고갈이 해소된다 (런북의 권장 조치와 맞춘다) */
    public static final int RECOVERY_POOL_SIZE = 20;

    private final AtomicReference<Fault> fault;
    private final AtomicInteger poolSize;

    public FaultState(@Value("${demo.pool-size:20}") int initialPoolSize) {
        this.poolSize = new AtomicInteger(initialPoolSize);
        this.fault = new AtomicReference<>(
                initialPoolSize < RECOVERY_POOL_SIZE ? Fault.DB_POOL_EXHAUSTION : Fault.NONE);
    }

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

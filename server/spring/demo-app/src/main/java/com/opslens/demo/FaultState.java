package com.opslens.demo;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Collections;
import java.util.EnumSet;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 주입된 장애 상태. 여러 장애가 겹칠 수 있다 (SPEC-compound-faults 6장).
 *   DB_POOL_EXHAUSTION : 시나리오 A. 요청 절반이 커넥션 획득 실패로 5xx
 *   PAYMENT_LATENCY    : 시나리오 B2. 외부 결제 API가 1.5초 이상 지연
 *   WORKER_HANG        : 시나리오 B. 작업 스레드가 모두 묶여 요청을 받지 못한다. 파드를 재시작하면 풀린다
 *   DB_SLOW            : 의존 대상(DB) 자체 지연. 쿼리마다 0.8~1.5초. 풀 설정은 그대로라 앱 조치로는 풀리지 않는다
 *
 * 앱 내부 장애(풀 고갈, 파드 멈춤)는 프로세스 메모리에만 있다. 그래서 파드 재시작(restart_pods)이 곧 그 장애의 해소다.
 * 의존 대상 장애(결제 지연, DB 지연)는 앱 밖의 문제라 재시작해도 남아야 한다. 새 파드는 시작할 때 고객사 네임스페이스의
 * ConfigMap(dependency-faults, 파일로 마운트)에서 읽는다. 떠 있는 파드에는 scripts/inject.py가 관리 API로 바로 넣는다.
 * 파일은 시작할 때만 읽는다. kubelet이 ConfigMap 볼륨을 갱신하는 데 1분쯤 걸려, 계속 읽으면 해제한 장애가 되살아난다.
 *
 * 시작할 때 설정(DEMO_POOL_SIZE)으로 풀 크기를 받는다. 20보다 작으면 처음부터 커넥션 풀 고갈 상태다.
 * 그래서 GitOps 고객사의 "잘못된 설정 배포"는 매니페스트의 이 값을 줄이는 커밋으로 만들고,
 * 롤백은 그 커밋을 되돌리는 PR로 한다.
 */
@Component
public class FaultState {

    public enum Fault { NONE, DB_POOL_EXHAUSTION, PAYMENT_LATENCY, WORKER_HANG, DB_SLOW }

    /** 앱 밖의 장애. 재시작해도 남는다 */
    public static final Set<Fault> DEPENDENCY_FAULTS = Collections.unmodifiableSet(
            EnumSet.of(Fault.PAYMENT_LATENCY, Fault.DB_SLOW));

    /** 이 크기보다 작으면 커넥션 풀이 고갈된다 */
    public static final int RECOVERY_POOL_SIZE = 20;

    private static final Logger log = LoggerFactory.getLogger(FaultState.class);

    private final AtomicReference<Set<Fault>> faults;
    private final AtomicInteger poolSize;

    public FaultState(@Value("${demo.pool-size:20}") int initialPoolSize,
            @Value("${demo.dependency-faults-file:}") String dependencyFaultsFile) {
        this.poolSize = new AtomicInteger(initialPoolSize);
        Set<Fault> initial = EnumSet.noneOf(Fault.class);
        if (initialPoolSize < RECOVERY_POOL_SIZE) {
            initial.add(Fault.DB_POOL_EXHAUSTION);
        }
        initial.addAll(readDependencyFaults(dependencyFaultsFile));
        this.faults = new AtomicReference<>(Collections.unmodifiableSet(initial));
    }

    /** 파일에 쉼표나 공백으로 적힌 의존 대상 장애. 파일이 없거나 비면 없음이다. 모르는 이름과 앱 내부 장애는 무시한다. */
    static Set<Fault> readDependencyFaults(String file) {
        Set<Fault> found = EnumSet.noneOf(Fault.class);
        if (file == null || file.isBlank() || !Files.isRegularFile(Path.of(file))) {
            return found;
        }
        try {
            for (String name : Files.readString(Path.of(file), StandardCharsets.UTF_8).split("[,\\s]+")) {
                Arrays.stream(Fault.values()).filter(f -> f.name().equals(name.strip()) && DEPENDENCY_FAULTS.contains(f))
                        .forEach(found::add);
            }
        } catch (IOException e) {
            log.warn("dependency faults file {} unreadable: {}", file, e.getMessage());
        }
        if (!found.isEmpty()) {
            log.info("dependency faults from {}: {}", file, found);
        }
        return found;
    }

    public boolean has(Fault fault) {
        return faults.get().contains(fault);
    }

    public Set<Fault> faults() {
        return faults.get();
    }

    /** 화면·로그용 한 값. 장애가 없으면 NONE, 여럿이면 첫 값 */
    public Fault fault() {
        return faults.get().stream().findFirst().orElse(Fault.NONE);
    }

    public int poolSize() {
        return poolSize.get();
    }

    /** 지금 장애를 모두 빼고 하나만 넣는다 (예전 단일 주입 API) */
    public void inject(Fault value) {
        clear();
        add(value);
    }

    public void add(Fault value) {
        if (value == Fault.NONE) {
            return;
        }
        if (value == Fault.DB_POOL_EXHAUSTION) {
            poolSize.set(10);
        }
        faults.updateAndGet(current -> with(current, value, true));
    }

    public void remove(Fault value) {
        faults.updateAndGet(current -> with(current, value, false));
    }

    public void clear() {
        faults.set(Collections.unmodifiableSet(EnumSet.noneOf(Fault.class)));
    }

    private static Set<Fault> with(Set<Fault> current, Fault value, boolean present) {
        Set<Fault> next = current.isEmpty() ? EnumSet.noneOf(Fault.class) : EnumSet.copyOf(current);
        if (present) {
            next.add(value);
        } else {
            next.remove(value);
        }
        return Collections.unmodifiableSet(next);
    }
}

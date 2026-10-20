package com.opslens.backend.task;

import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 작업 전달 대기열을 agent에 발행한다 (SPEC-kafka-scale 4.3(1)).
 * - 작업을 만든 직후 kick()으로 바로 발행해 평소 지연을 없앤다. 요청 스레드를 붙잡지 않게 별도 스레드에서 보낸다
 * - 실패하면 2^시도 초 뒤에 다시 보낸다. 1초마다 도는 점검이 차례가 된 건과 backend가 죽어 남은 건을 보낸다
 * - MAX_ATTEMPTS번 모두 실패하면 작업을 FAILED로 끝낸다. QUEUED로 영원히 남는 작업을 만들지 않는다
 */
@Component
public class TaskOutboxRelay {

    static final int MAX_ATTEMPTS = 5;
    private static final int BATCH = 20;
    private static final Logger log = LoggerFactory.getLogger(TaskOutboxRelay.class);

    private final TaskOutboxRepository outbox;
    private final TaskRepository tasks;
    private final TaskDispatcher dispatcher;
    private final ExecutorService sender = Executors.newVirtualThreadPerTaskExecutor();

    public TaskOutboxRelay(TaskOutboxRepository outbox, TaskRepository tasks, TaskDispatcher dispatcher) {
        this.outbox = outbox;
        this.tasks = tasks;
        this.dispatcher = dispatcher;
    }

    /** 트랜잭션이 커밋된 뒤에 부른다. 커밋 전에 부르면 아직 보이지 않는 행이라 집지 못한다. */
    public void kick(UUID taskId) {
        sender.execute(() -> {
            try {
                outbox.claim(taskId).ifPresent(this::send);
            } catch (RuntimeException e) {
                log.warn("task {} immediate dispatch skipped, the scheduled relay will retry", taskId, e);
            }
        });
    }

    @Scheduled(fixedDelay = 1000, initialDelay = 1000)
    void relayPending() {
        outbox.claim(BATCH).forEach(this::send);
    }

    void send(TaskOutboxRepository.Claimed claimed) {
        UUID taskId = claimed.message().taskId();
        try {
            dispatcher.dispatch(claimed.message());
            outbox.markSent(claimed.id());
        } catch (RuntimeException e) {
            String error = e.getClass().getSimpleName() + ": " + e.getMessage();
            if (claimed.attempts() >= MAX_ATTEMPTS) {
                log.error("task {} dispatch failed {} times, giving up", taskId, claimed.attempts(), e);
                outbox.markFailed(claimed.id(), error);
                tasks.complete(taskId, TaskRecord.FAILED, "작업을 agent에 전달하지 못했다", null, null, null);
            } else {
                long delay = 1L << claimed.attempts();
                log.warn("task {} dispatch failed (attempt {}), retrying in {}s: {}", taskId, claimed.attempts(),
                        delay, error);
                outbox.retryLater(claimed.id(), error, delay);
            }
        }
    }
}

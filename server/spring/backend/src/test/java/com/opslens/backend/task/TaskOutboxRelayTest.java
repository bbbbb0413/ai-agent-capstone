package com.opslens.backend.task;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class TaskOutboxRelayTest {
    private final TaskOutboxRepository outbox = mock(TaskOutboxRepository.class);
    private final TaskRepository tasks = mock(TaskRepository.class);
    private final TaskDispatcher dispatcher = mock(TaskDispatcher.class);
    private final TaskOutboxRelay relay = new TaskOutboxRelay(outbox, tasks, dispatcher);
    private final UUID taskId = UUID.randomUUID();
    private final TaskMessage message = new TaskMessage(taskId, "tenant-a", "GOLD", null, "QUESTION", "q", "trace");

    @Test
    void sentMessageIsMarkedSent() {
        relay.send(new TaskOutboxRepository.Claimed(1L, message, 1));
        verify(dispatcher).dispatch(message);
        verify(outbox).markSent(1L);
        verifyNoInteractions(tasks);
    }

    @Test
    void failedSendIsRetriedWithBackoff() {
        doThrow(new IllegalStateException("broker down")).when(dispatcher).dispatch(any());
        relay.send(new TaskOutboxRepository.Claimed(1L, message, 3));
        verify(outbox).retryLater(eq(1L), contains("broker down"), eq(8L));   // 2^3초 뒤
        verify(outbox, never()).markSent(anyLong());
        verifyNoInteractions(tasks);
    }

    @Test
    void lastFailedAttemptFailsTheTaskSoItIsNotLeftQueued() {
        doThrow(new IllegalStateException("broker down")).when(dispatcher).dispatch(any());
        relay.send(new TaskOutboxRepository.Claimed(1L, message, TaskOutboxRelay.MAX_ATTEMPTS));
        verify(outbox).markFailed(eq(1L), contains("broker down"));
        verify(tasks).complete(eq(taskId), eq(TaskRecord.FAILED), anyString(), isNull(), isNull(), isNull());
    }

    @Test
    void scheduledRelaySendsEveryClaimedMessage() {
        TaskMessage other = new TaskMessage(UUID.randomUUID(), "tenant-b", "SILVER", null, "QUESTION", "q", "t2");
        when(outbox.claim(anyInt())).thenReturn(List.of(new TaskOutboxRepository.Claimed(1L, message, 1),
                new TaskOutboxRepository.Claimed(2L, other, 2)));
        relay.relayPending();
        verify(dispatcher).dispatch(message);
        verify(dispatcher).dispatch(other);
        verify(outbox).markSent(1L);
        verify(outbox).markSent(2L);
    }
}

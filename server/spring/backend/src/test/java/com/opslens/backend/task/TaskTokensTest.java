package com.opslens.backend.task;

import static org.junit.jupiter.api.Assertions.*;

import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** 작업 토큰은 실행 중인 작업에만 주고, 만료·다른 키 서명은 받지 않는다. */
class TaskTokensTest {

    private static TaskRecord task(String status) {
        return new TaskRecord(UUID.randomUUID(), "tenant-a", null, "QUESTION", "q", status, null, null, null, null,
                null, null, OffsetDateTime.now(), OffsetDateTime.now(), null, null);
    }

    @Test
    void onlyQueuedOrRunningTasksGetAToken() {
        TaskTokens tokens = new TaskTokens();
        for (String status : new String[] {TaskRecord.QUEUED, TaskRecord.RUNNING}) {
            TaskRecord task = task(status);
            TaskTokens.Claims claims = tokens.verify(tokens.issue(task).token());
            assertEquals(task.id(), claims.taskId());
            assertEquals("tenant-a", claims.tenantId());
        }
        for (String status : new String[] {TaskRecord.DONE, TaskRecord.FAILED, TaskRecord.WAITING_APPROVAL,
                TaskRecord.CHANGE_PENDING}) {
            var error = assertThrows(ResponseStatusException.class, () -> tokens.issue(task(status)));
            assertEquals(HttpStatus.CONFLICT, error.getStatusCode(), status);
        }
    }

    /** 같은 키로 시각만 옮긴다 */
    private static final class MovableClock extends Clock {
        Instant now;

        MovableClock(Instant now) {
            this.now = now;
        }

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public ZoneOffset getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(java.time.ZoneId zone) {
            return this;
        }
    }

    @Test
    void expiredTokenIsRejected() {
        MovableClock clock = new MovableClock(Instant.parse("2026-10-23T00:00:00Z"));
        TaskTokens tokens = new TaskTokens(clock);
        String token = tokens.issue(task(TaskRecord.RUNNING)).token();
        clock.now = clock.now.plus(TaskTokens.TTL).minusSeconds(1);
        assertNotNull(tokens.verify(token));
        clock.now = clock.now.plusSeconds(2);
        assertEquals(HttpStatus.UNAUTHORIZED,
                assertThrows(ResponseStatusException.class, () -> tokens.verify(token)).getStatusCode());
    }

    @Test
    void jwksPublishesOnlyThePublicKey() {
        var keys = new TaskTokens().jwks().toString();
        assertTrue(keys.contains("\"kty\":\"RSA\"") || keys.contains("kty=RSA"));
        assertFalse(keys.contains("\"d\"") || keys.contains(" d="));   // 개인키 성분
    }
}

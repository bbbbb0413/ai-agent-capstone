package com.opslens.backend.task;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 작업 전달 대기열 (Transactional Outbox). 발행할 건은 임대(LEASE)해서 가져간다.
 * 임대는 짧은 UPDATE 한 문장이라 행 잠금을 전송 동안 쥐지 않는다. 여러 backend가 떠도 같은 건을 동시에 집지 않고,
 * 전송 중에 backend가 죽으면 임대가 끝난 뒤 다른 인스턴스가 다시 가져간다.
 */
@Repository
public class TaskOutboxRepository {

    /** 발행 한 번에 쓸 수 있는 시간. 발행기 제한 시간(Kafka 5초, HTTP 연결)보다 넉넉해야 한다. */
    static final int LEASE_SECONDS = 30;

    public record Claimed(long id, TaskMessage message, int attempts) {}

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public TaskOutboxRepository(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** 작업 생성과 같은 트랜잭션 안에서 부른다. */
    public void enqueue(TaskMessage message) {
        jdbc.sql("INSERT INTO task_outbox (task_id, payload, trace_id) VALUES (?, ?::jsonb, ?)")
                .params(message.taskId(), toJson(message), message.traceId()).update();
    }

    /** 발행할 차례인 건을 limit개까지 임대한다. */
    public List<Claimed> claim(int limit) {
        return jdbc.sql("""
                        UPDATE task_outbox SET attempts = attempts + 1,
                               next_attempt_at = now() + make_interval(secs => ?)
                        WHERE id IN (SELECT id FROM task_outbox
                                     WHERE status = 'PENDING' AND next_attempt_at <= now()
                                     ORDER BY next_attempt_at, id LIMIT ? FOR UPDATE SKIP LOCKED)
                        RETURNING id, payload, attempts
                        """)
                .params(LEASE_SECONDS, limit).query(this::toClaimed).list();
    }

    /** 방금 만든 작업 하나를 바로 임대한다 (커밋 직후 즉시 발행). 이미 다른 쪽이 가져갔으면 비어 있다. */
    public Optional<Claimed> claim(UUID taskId) {
        return jdbc.sql("""
                        UPDATE task_outbox SET attempts = attempts + 1,
                               next_attempt_at = now() + make_interval(secs => ?)
                        WHERE id = (SELECT id FROM task_outbox
                                    WHERE task_id = ? AND status = 'PENDING' AND next_attempt_at <= now()
                                    FOR UPDATE SKIP LOCKED)
                        RETURNING id, payload, attempts
                        """)
                .params(LEASE_SECONDS, taskId).query(this::toClaimed).optional();
    }

    public void markSent(long id) {
        jdbc.sql("UPDATE task_outbox SET status = 'SENT', sent_at = now(), last_error = NULL WHERE id = ?")
                .param(id).update();
    }

    public void retryLater(long id, String error, long delaySeconds) {
        jdbc.sql("""
                        UPDATE task_outbox SET last_error = ?, next_attempt_at = now() + make_interval(secs => ?)
                        WHERE id = ?
                        """)
                .params(error, delaySeconds, id).update();
    }

    public void markFailed(long id, String error) {
        jdbc.sql("UPDATE task_outbox SET status = 'FAILED', last_error = ? WHERE id = ?").params(error, id).update();
    }

    private Claimed toClaimed(ResultSet rs, int rowNum) throws SQLException {
        try {
            return new Claimed(rs.getLong("id"), json.readValue(rs.getString("payload"), TaskMessage.class),
                    rs.getInt("attempts"));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(e);
        }
    }
}

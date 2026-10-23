package com.opslens.backend.document;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 색인 요청 전달 대기열 (Transactional Outbox, SPEC-document-store 3.2). TaskOutboxRepository와 같은 임대 방식이다.
 * 문서 버전과 같은 트랜잭션에 쓰므로 저장은 됐는데 요청이 빠지는 일이 없다.
 */
@Repository
public class DocumentOutbox {

    public static final String INDEX = "INDEX";
    public static final String DELETE = "DELETE";

    static final int LEASE_SECONDS = 30;

    /** Kafka 메시지. 버전 ID와 종류만 싣는다. 원본·비밀 값은 싣지 않는다 */
    public record Message(String kind, UUID documentId, UUID documentVersionId) {}

    public record Claimed(long id, Message message, int attempts) {}

    private final JdbcClient jdbc;

    public DocumentOutbox(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void enqueue(Message message) {
        jdbc.sql("INSERT INTO document_outbox (document_id, document_version_id, kind) VALUES (?, ?, ?)")
                .params(message.documentId(), message.documentVersionId(), message.kind()).update();
    }

    public List<Claimed> claim(int limit) {
        return jdbc.sql("""
                        UPDATE document_outbox SET attempts = attempts + 1,
                               next_attempt_at = now() + make_interval(secs => ?)
                        WHERE id IN (SELECT id FROM document_outbox
                                     WHERE status = 'PENDING' AND next_attempt_at <= now()
                                     ORDER BY next_attempt_at, id LIMIT ? FOR UPDATE SKIP LOCKED)
                        RETURNING id, document_id, document_version_id, kind, attempts
                        """)
                .params(LEASE_SECONDS, limit).query(DocumentOutbox::toClaimed).list();
    }

    /** 방금 넣은 문서의 요청을 바로 임대한다 (커밋 직후 즉시 발행) */
    public Optional<Claimed> claim(UUID documentId) {
        return jdbc.sql("""
                        UPDATE document_outbox SET attempts = attempts + 1,
                               next_attempt_at = now() + make_interval(secs => ?)
                        WHERE id = (SELECT id FROM document_outbox
                                    WHERE document_id = ? AND status = 'PENDING' AND next_attempt_at <= now()
                                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
                        RETURNING id, document_id, document_version_id, kind, attempts
                        """)
                .params(LEASE_SECONDS, documentId).query(DocumentOutbox::toClaimed).optional();
    }

    /** 이 문서에 아직 보내지 않은 요청이 있나 (점검이 같은 요청을 겹쳐 넣지 않게) */
    public boolean hasPending(UUID documentId) {
        return jdbc.sql("SELECT count(*) FROM document_outbox WHERE document_id = ? AND status = 'PENDING'")
                .param(documentId).query(Integer.class).single() > 0;
    }

    public void markSent(long id) {
        jdbc.sql("UPDATE document_outbox SET status = 'SENT', sent_at = now(), last_error = NULL WHERE id = ?")
                .param(id).update();
    }

    public void retryLater(long id, String error, long delaySeconds) {
        jdbc.sql("UPDATE document_outbox SET last_error = ?, next_attempt_at = now() + make_interval(secs => ?) WHERE id = ?")
                .params(error, delaySeconds, id).update();
    }

    public void markFailed(long id, String error) {
        jdbc.sql("UPDATE document_outbox SET status = 'FAILED', last_error = ? WHERE id = ?").params(error, id).update();
    }

    private static Claimed toClaimed(ResultSet rs, int n) throws SQLException {
        return new Claimed(rs.getLong("id"), new Message(rs.getString("kind"), rs.getObject("document_id", UUID.class),
                rs.getObject("document_version_id", UUID.class)), rs.getInt("attempts"));
    }
}

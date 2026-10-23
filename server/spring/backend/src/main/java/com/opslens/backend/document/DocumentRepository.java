package com.opslens.backend.document;

import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

/** 문서와 버전 메타데이터 (V18). 원본은 DocumentStorage에 있다. */
@Repository
public class DocumentRepository {

    public static final String PENDING = "PENDING";
    public static final String INDEXED = "INDEXED";
    public static final String FAILED = "FAILED";
    public static final String SUPERSEDED = "SUPERSEDED";

    /** 다시 시도할 수 있는 실패를 다시 보내는 간격과 횟수 (SPEC-document-store 3.2) */
    static final int RETRY_MINUTES = 5;
    static final int MAX_ATTEMPTS = 3;

    public record Document(UUID id, String tenantId, String docId, String docType, String title,
            Integer activeVersion, OffsetDateTime deletedAt, String deletedBy, OffsetDateTime chunksRemovedAt,
            String createdBy, OffsetDateTime createdAt) {

        public boolean deleted() {
            return deletedAt != null;
        }
    }

    public record Version(UUID id, UUID documentId, int version, String originalFilename, String format,
            String objectKey, String sha256, int sizeBytes, Integer restoredFrom, String uploadedBy,
            OffsetDateTime uploadedAt, String indexStatus, String indexStage, String indexError, Boolean retryable,
            int attempts, Integer chunkCount, List<String> clauses, boolean hasExtracted, OffsetDateTime indexedAt) {}

    /** 목록 한 줄: 문서와 최신 버전 */
    public record Summary(Document document, Version latest) {}

    private final JdbcClient jdbc;

    public DocumentRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    // ── 조회 ───────────────────────────────────────────────────

    public Optional<Document> find(String tenantId, String docId) {
        return jdbc.sql("SELECT * FROM documents WHERE tenant_id = ? AND doc_id = ?").params(tenantId, docId)
                .query(DocumentRepository::toDocument).optional();
    }

    public Document get(UUID id) {
        return jdbc.sql("SELECT * FROM documents WHERE id = ?").param(id).query(DocumentRepository::toDocument)
                .optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "문서를 찾을 수 없다"));
    }

    /** tenantIds가 null이면 전부. 삭제한 문서는 빼고, 범위·제목 순이다 */
    public List<Summary> list(List<String> tenantIds) {
        return jdbc.sql("""
                        SELECT d.*, v.*, v.id AS version_id FROM documents d
                        JOIN LATERAL (SELECT * FROM document_versions WHERE document_id = d.id
                                      ORDER BY version DESC LIMIT 1) v ON true
                        WHERE d.deleted_at IS NULL AND (?::text[] IS NULL OR d.tenant_id = ANY(?::text[]))
                        ORDER BY d.tenant_id, d.title
                        """)
                .params(array(tenantIds), array(tenantIds))
                .query((rs, n) -> new Summary(toDocument(rs, n), toVersion(rs, "version_id"))).list();
    }

    public List<Version> versions(UUID documentId) {
        return jdbc.sql("SELECT * FROM document_versions WHERE document_id = ? ORDER BY version DESC")
                .param(documentId).query(DocumentRepository::toVersion).list();
    }

    public Version version(UUID documentId, int version) {
        return jdbc.sql("SELECT * FROM document_versions WHERE document_id = ? AND version = ?")
                .params(documentId, version).query(DocumentRepository::toVersion).optional()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "버전을 찾을 수 없다: v" + version));
    }

    public Version versionById(UUID id) {
        return jdbc.sql("SELECT * FROM document_versions WHERE id = ?").param(id).query(DocumentRepository::toVersion)
                .optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "버전을 찾을 수 없다"));
    }

    public int latestVersion(UUID documentId) {
        return jdbc.sql("SELECT coalesce(max(version), 0) FROM document_versions WHERE document_id = ?")
                .param(documentId).query(Integer.class).single();
    }

    /** 색인을 다시 요청할 버전: 오래 대기 중이거나, 다시 시도할 차례가 된 실패. 최신이 아닌 버전은 뺀다 */
    public List<Version> waiting(int pendingMinutes) {
        return jdbc.sql("""
                        SELECT v.* FROM document_versions v JOIN documents d ON d.id = v.document_id
                        WHERE d.deleted_at IS NULL
                          AND v.version = (SELECT max(version) FROM document_versions WHERE document_id = v.document_id)
                          AND ((v.index_status = 'PENDING' AND v.uploaded_at < now() - make_interval(mins => ?)
                                AND (v.last_attempt_at IS NULL OR v.last_attempt_at < now() - make_interval(mins => ?)))
                            OR (v.index_status = 'FAILED' AND v.retryable AND v.attempts < ?
                                AND v.last_attempt_at < now() - make_interval(mins => ?)))
                        ORDER BY v.uploaded_at LIMIT 50
                        """)
                .params(pendingMinutes, pendingMinutes, MAX_ATTEMPTS, RETRY_MINUTES)
                .query(DocumentRepository::toVersion).list();
    }

    /** 삭제했는데 조각이 아직 남은 문서 */
    public List<Document> deletedWithChunks() {
        return jdbc.sql("SELECT * FROM documents WHERE deleted_at IS NOT NULL AND chunks_removed_at IS NULL LIMIT 50")
                .query(DocumentRepository::toDocument).list();
    }

    /** 공통 표준 런북 검사의 기준: 검색 버전의 조항 번호. 검색 버전이 없으면 비어 있다 */
    public Optional<List<String>> activeClauses(Document document) {
        if (document.activeVersion() == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(version(document.id(), document.activeVersion()).clauses());
    }

    // ── 변경 ───────────────────────────────────────────────────

    public void insertDocument(Document d) {
        jdbc.sql("""
                        INSERT INTO documents (id, tenant_id, doc_id, doc_type, title, created_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """)
                .params(d.id(), d.tenantId(), d.docId(), d.docType(), d.title(), d.createdBy()).update();
    }

    public void insertVersion(Version v) {
        jdbc.sql("""
                        INSERT INTO document_versions (id, document_id, version, original_filename, format, object_key,
                                                       sha256, size_bytes, restored_from, uploaded_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """)
                .params(v.id(), v.documentId(), v.version(), v.originalFilename(), v.format(), v.objectKey(),
                        v.sha256(), v.sizeBytes(), v.restoredFrom(), v.uploadedBy())
                .update();
    }

    public void markDeleted(UUID documentId, String by) {
        jdbc.sql("UPDATE documents SET deleted_at = now(), deleted_by = ? WHERE id = ? AND deleted_at IS NULL")
                .params(by, documentId).update();
    }

    public void markChunksRemoved(UUID documentId) {
        jdbc.sql("UPDATE documents SET chunks_removed_at = now() WHERE id = ? AND deleted_at IS NOT NULL")
                .param(documentId).update();
    }

    /** 색인을 다시 요청했다 (점검이 같은 버전을 연달아 보내지 않게 시각을 남긴다) */
    public void markRequested(UUID versionId) {
        jdbc.sql("UPDATE document_versions SET last_attempt_at = now() WHERE id = ?").param(versionId).update();
    }

    /**
     * 색인 성공. 검색 버전보다 낮은 버전의 결과는 받지 않는다(늦게 끝난 지난 버전이 최신을 덮지 않게).
     * 받으면 검색 버전을 바꾸고, 그보다 낮은 대기·실패 버전은 SUPERSEDED로 둔다.
     */
    public boolean recordIndexed(Version v, int chunkCount, List<String> clauses, boolean hasExtracted) {
        int updated = jdbc.sql("""
                        UPDATE documents SET active_version = ?
                        WHERE id = ? AND (active_version IS NULL OR active_version < ?)
                        """)
                .params(v.version(), v.documentId(), v.version()).update();
        if (updated == 0) {
            return false;
        }
        jdbc.sql("""
                        UPDATE document_versions SET index_status = 'INDEXED', index_stage = NULL, index_error = NULL,
                               retryable = NULL, chunk_count = ?, clauses = ?::text[], has_extracted = ?,
                               indexed_at = now(), last_attempt_at = now(), attempts = attempts + 1
                        WHERE id = ?
                        """)
                .params(chunkCount, array(clauses), hasExtracted, v.id()).update();
        jdbc.sql("""
                        UPDATE document_versions SET index_status = 'SUPERSEDED'
                        WHERE document_id = ? AND version < ? AND index_status IN ('PENDING', 'FAILED')
                        """)
                .params(v.documentId(), v.version()).update();
        return true;
    }

    /** 색인 실패. 이미 성공했거나 밀려난 버전이면 받지 않는다 */
    public boolean recordFailed(UUID versionId, String stage, String error, boolean retryable) {
        return jdbc.sql("""
                        UPDATE document_versions SET index_status = 'FAILED', index_stage = ?, index_error = ?,
                               retryable = ?, attempts = attempts + 1, last_attempt_at = now()
                        WHERE id = ? AND index_status IN ('PENDING', 'FAILED')
                        """)
                .params(stage, error, retryable, versionId).update() > 0;
    }

    // ── 변환 ───────────────────────────────────────────────────

    private static String array(List<String> values) {
        return values == null ? null : "{" + String.join(",", values.stream()
                .map(v -> "\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\"").toList()) + "}";
    }

    static Document toDocument(ResultSet rs, int n) throws SQLException {
        return new Document(rs.getObject("id", UUID.class), rs.getString("tenant_id"), rs.getString("doc_id"),
                rs.getString("doc_type"), rs.getString("title"), rs.getObject("active_version", Integer.class),
                rs.getObject("deleted_at", OffsetDateTime.class), rs.getString("deleted_by"),
                rs.getObject("chunks_removed_at", OffsetDateTime.class), rs.getString("created_by"),
                rs.getObject("created_at", OffsetDateTime.class));
    }

    static Version toVersion(ResultSet rs, int n) throws SQLException {
        return toVersion(rs, "id");
    }

    /** idColumn: 문서와 붙여 읽을 때는 두 표의 id가 겹쳐 별칭을 쓴다 */
    static Version toVersion(ResultSet rs, String idColumn) throws SQLException {
        Array clauses = rs.getArray("clauses");
        return new Version(rs.getObject(idColumn, UUID.class),
                rs.getObject("document_id", UUID.class), rs.getInt("version"), rs.getString("original_filename"),
                rs.getString("format"), rs.getString("object_key"), rs.getString("sha256"), rs.getInt("size_bytes"),
                rs.getObject("restored_from", Integer.class), rs.getString("uploaded_by"),
                rs.getObject("uploaded_at", OffsetDateTime.class), rs.getString("index_status"),
                rs.getString("index_stage"), rs.getString("index_error"), rs.getObject("retryable", Boolean.class),
                rs.getInt("attempts"), rs.getObject("chunk_count", Integer.class),
                clauses == null ? null : Arrays.asList((String[]) clauses.getArray()),
                rs.getBoolean("has_extracted"), rs.getObject("indexed_at", OffsetDateTime.class));
    }
}

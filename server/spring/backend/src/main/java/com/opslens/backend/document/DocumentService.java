package com.opslens.backend.document;

import static com.opslens.backend.document.DocumentFiles.COMMON;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.tenant.TenantRepository;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.server.ResponseStatusException;

/**
 * 근거 문서 업로드·버전·되돌리기·삭제 (SPEC-document-store 4.1). 권한은 여기서만 판단한다.
 *   보기    관리자: 전부. 엔지니어: 공통 + 담당 고객사
 *   바꾸기  공통: 관리자만. 고객사: 관리자와 그 고객사를 담당하는 엔지니어
 * 원본을 저장하고 버전을 PENDING으로 기록한 뒤 색인을 요청한다. 파싱·임베딩은 indexer가 한다.
 */
@Service
public class DocumentService {

    /** 색인 요청 전달. Kafka면 outbox와 relay, Kafka 없이 돌면 indexer가 대기 목록을 가져가므로 하는 일이 없다 */
    public interface IndexRequests {
        void request(DocumentOutbox.Message message);

        void kick(UUID documentId);
    }

    @Component
    @ConditionalOnProperty(name = "opslens.dispatch-mode", havingValue = "http")
    static class PolledIndexRequests implements IndexRequests {
        @Override
        public void request(DocumentOutbox.Message message) {}

        @Override
        public void kick(UUID documentId) {}
    }

    public record Upload(String tenantId, String docId, String docType, String title, String filename, byte[] content) {}

    private final DocumentRepository documents;
    private final DocumentStorage storage;
    private final IndexRequests index;
    private final TenantRepository tenants;
    private final AuditLog audit;

    public DocumentService(DocumentRepository documents, DocumentStorage storage, IndexRequests index,
            TenantRepository tenants, AuditLog audit) {
        this.documents = documents;
        this.storage = storage;
        this.index = index;
        this.tenants = tenants;
        this.audit = audit;
    }

    // ── 권한 ───────────────────────────────────────────────────

    static boolean canView(CurrentUser user, String tenantId) {
        return user.isAdmin() || (user.isMspStaff() && (COMMON.equals(tenantId) || user.canAccess(tenantId)));
    }

    static boolean canEdit(CurrentUser user, String tenantId) {
        if (COMMON.equals(tenantId)) {
            return user.isAdmin();
        }
        return user.isAdmin() || (CurrentUser.MSP_ENGINEER.equals(user.role()) && user.canAccess(tenantId));
    }

    private static void requireView(CurrentUser user, String tenantId) {
        if (!canView(user, tenantId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "이 범위의 문서를 볼 수 없다: " + tenantId);
        }
    }

    private static void requireEdit(CurrentUser user, String tenantId) {
        if (!canEdit(user, tenantId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, COMMON.equals(tenantId)
                    ? "공통 문서는 MSP 관리자만 바꾼다"
                    : "담당하는 고객사의 문서만 바꾼다: " + tenantId);
        }
    }

    // ── 조회 ───────────────────────────────────────────────────

    public List<DocumentRepository.Summary> list(CurrentUser user, String tenantId) {
        user.requireMspStaff();
        if (tenantId != null && !tenantId.isBlank()) {
            requireView(user, tenantId);
            return documents.list(List.of(tenantId));
        }
        if (user.isAdmin()) {
            return documents.list(null);
        }
        var scopes = new java.util.ArrayList<>(user.tenants());
        scopes.add(COMMON);
        return documents.list(scopes);
    }

    /** 문서와 버전 이력. 화면이 버튼을 고를 수 있게 이 사용자가 바꿀 수 있는지도 준다 */
    public Map<String, Object> detail(CurrentUser user, UUID id) {
        DocumentRepository.Document document = viewable(user, id);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("document", document);
        result.put("versions", documents.versions(id));
        result.put("canEdit", canEdit(user, document.tenantId()) && !document.deleted());
        return result;
    }

    public record FileContent(String filename, String contentType, byte[] content) {}

    public FileContent original(CurrentUser user, UUID id, int version) {
        viewable(user, id);
        DocumentRepository.Version v = documents.version(id, version);
        return new FileContent(v.originalFilename(), DocumentFiles.CONTENT_TYPES.get(v.format()), storage.get(v.objectKey()));
    }

    /** 추출 본문. Markdown·텍스트는 원본이 곧 본문이다. PDF·Word는 색인에 성공한 버전만 있다 */
    public String extracted(CurrentUser user, UUID id, int version) {
        viewable(user, id);
        return extractedText(documents.version(id, version));
    }

    String extractedText(DocumentRepository.Version v) {
        if (v.format().equals("md") || v.format().equals("txt")) {
            return new String(storage.get(v.objectKey()), java.nio.charset.StandardCharsets.UTF_8);
        }
        if (!v.hasExtracted()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "아직 색인되지 않아 추출 본문이 없다: v" + v.version());
        }
        return new String(storage.get(DocumentStorage.extractedKey(v.objectKey())), java.nio.charset.StandardCharsets.UTF_8);
    }

    private DocumentRepository.Document viewable(CurrentUser user, UUID id) {
        user.requireMspStaff();
        DocumentRepository.Document document = documents.get(id);
        requireView(user, document.tenantId());
        return document;
    }

    // ── 변경 ───────────────────────────────────────────────────

    /** 새 문서(버전 1). 같은 범위에 같은 문서 ID가 있으면 409 */
    @Transactional
    public Map<String, Object> create(CurrentUser user, Upload upload) {
        user.requireMspStaff();
        String tenantId = upload.tenantId();
        requireEdit(user, tenantId);
        if (!COMMON.equals(tenantId) && tenants.find(tenantId).isEmpty()) {
            throw DocumentFiles.unprocessable("없는 고객사다: " + tenantId);
        }
        DocumentFiles.checkType(tenantId, upload.docType());
        DocumentFiles.checkDocId(upload.docId());
        String title = DocumentFiles.checkTitle(upload.title());
        DocumentFiles.Checked file = DocumentFiles.check(upload.filename(), upload.content());
        if (documents.find(tenantId, upload.docId()).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "이미 있는 문서다: %s/%s. 새 버전으로 올린다".formatted(tenantId, upload.docId()));
        }
        var document = new DocumentRepository.Document(UUID.randomUUID(), tenantId, upload.docId(), upload.docType(),
                title, null, null, null, null, user.username(), null);
        try {
            documents.insertDocument(document);
        } catch (DuplicateKeyException e) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "이미 있는 문서다: " + upload.docId());
        }
        return addVersion(document, file, null, user.username());
    }

    /** 새 버전. 형식은 이전 버전과 달라도 된다 */
    @Transactional
    public Map<String, Object> newVersion(CurrentUser user, UUID id, String filename, byte[] content) {
        DocumentRepository.Document document = editable(user, id);
        return addVersion(document, DocumentFiles.check(filename, content), null, user.username());
    }

    /** 고른 버전을 복사한 새 버전. 이력이 앞으로만 늘어 누가 무엇을 되돌렸는지 남는다 */
    @Transactional
    public Map<String, Object> restore(CurrentUser user, UUID id, int version) {
        DocumentRepository.Document document = editable(user, id);
        DocumentRepository.Version from = documents.version(id, version);
        int next = documents.latestVersion(id) + 1;
        String key = DocumentStorage.originalKey(document.tenantId(), document.docId(), next, from.format());
        storage.copy(from.objectKey(), key);
        var v = new DocumentRepository.Version(UUID.randomUUID(), id, next, from.originalFilename(), from.format(), key,
                from.sha256(), from.sizeBytes(), version, user.username(), null, null, null, null, null, 0, null, null,
                false, null);
        documents.insertVersion(v);
        requestIndex(document, v);
        audit.record(auditTenant(document), user.username(), "DOCUMENT_RESTORED",
                Map.of("document", document.docId(), "version", next, "restoredFrom", version), null);
        return Map.of("documentId", id, "version", next);
    }

    @Transactional
    public void delete(CurrentUser user, UUID id) {
        DocumentRepository.Document document = editable(user, id);
        documents.markDeleted(id, user.username());
        index.request(new DocumentOutbox.Message(DocumentOutbox.DELETE, id, null));
        afterCommit(() -> index.kick(id));
        audit.record(auditTenant(document), user.username(), "DOCUMENT_DELETED", Map.of("document", document.docId()),
                null);
    }

    /** 시드 (DocumentSeeder). 권한 검사 없이 버전 1을 만든다. 이미 있으면 건드리지 않는다 */
    @Transactional
    public boolean seed(String tenantId, String docId, String docType, String title, String filename, byte[] content) {
        if (documents.find(tenantId, docId).isPresent()) {
            return false;
        }
        var document = new DocumentRepository.Document(UUID.randomUUID(), tenantId, docId, docType, title, null, null,
                null, null, "seed", null);
        documents.insertDocument(document);
        addVersion(document, DocumentFiles.check(filename, content), null, "seed");
        return true;
    }

    private DocumentRepository.Document editable(CurrentUser user, UUID id) {
        user.requireMspStaff();
        DocumentRepository.Document document = documents.get(id);
        requireEdit(user, document.tenantId());
        if (document.deleted()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "삭제한 문서다: " + document.docId());
        }
        return document;
    }

    private Map<String, Object> addVersion(DocumentRepository.Document document, DocumentFiles.Checked file,
            Integer restoredFrom, String by) {
        int next = documents.latestVersion(document.id()) + 1;
        String key = DocumentStorage.originalKey(document.tenantId(), document.docId(), next, file.format());
        // 객체를 먼저 올린다. 트랜잭션이 실패하면 쓰이지 않는 객체가 남을 뿐이고, 버전 행이 없는 객체를 가리키는 일은 없다
        storage.put(key, file.content(), DocumentFiles.CONTENT_TYPES.get(file.format()));
        var v = new DocumentRepository.Version(UUID.randomUUID(), document.id(), next, file.filename(), file.format(),
                key, sha256(file.content()), file.content().length, restoredFrom, by, null, null, null, null, null, 0,
                null, null, false, null);
        documents.insertVersion(v);
        requestIndex(document, v);
        audit.record(auditTenant(document), by, "DOCUMENT_UPLOADED",
                Map.of("document", document.docId(), "version", next, "format", file.format()), null);
        return Map.of("documentId", document.id(), "version", next);
    }

    private void requestIndex(DocumentRepository.Document document, DocumentRepository.Version v) {
        index.request(new DocumentOutbox.Message(DocumentOutbox.INDEX, document.id(), v.id()));
        afterCommit(() -> index.kick(document.id()));
    }

    /** 감사 로그의 고객사 칸. 공통 문서는 고객사가 없다 */
    private static String auditTenant(DocumentRepository.Document document) {
        return COMMON.equals(document.tenantId()) ? null : document.tenantId();
    }

    private static void afterCommit(Runnable action) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    action.run();
                }
            });
        } else {
            action.run();
        }
    }

    static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}

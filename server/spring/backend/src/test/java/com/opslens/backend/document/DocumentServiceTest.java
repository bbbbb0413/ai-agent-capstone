package com.opslens.backend.document;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** 문서 권한 표와 버전·되돌리기·삭제 (SPEC-document-store 4.1) */
class DocumentServiceTest {

    private final DocumentRepository documents = mock(DocumentRepository.class);
    private final DocumentStorage storage = mock(DocumentStorage.class);
    private final DocumentService.IndexRequests index = mock(DocumentService.IndexRequests.class);
    private final TenantRepository tenants = mock(TenantRepository.class);
    private final AuditLog audit = mock(AuditLog.class);
    private DocumentService service;

    private static final CurrentUser ADMIN = new CurrentUser(1, "admin", CurrentUser.MSP_ADMIN, List.of());
    private static final CurrentUser ENGINEER_A = new CurrentUser(2, "eng-a", CurrentUser.MSP_ENGINEER, List.of("tenant-a"));
    private static final CurrentUser VIEWER_A = new CurrentUser(3, "viewer-a", CurrentUser.TENANT_VIEWER, List.of("tenant-a"));
    private static final byte[] MD = "# 런북\n\n## 1. 확인\n본문\n".getBytes(StandardCharsets.UTF_8);

    @BeforeEach
    void setup() {
        service = new DocumentService(documents, storage, index, tenants, audit);
        when(tenants.find(anyString())).thenReturn(Optional.of(mock(Tenant.class)));
        when(documents.find(anyString(), anyString())).thenReturn(Optional.empty());
    }

    private DocumentService.Upload upload(String tenant, String type) {
        return new DocumentService.Upload(tenant, "runbook-x", type, "런북", "runbook-x.md", MD);
    }

    private static HttpStatus status(Runnable call) {
        return HttpStatus.valueOf(assertThrows(ResponseStatusException.class, call::run).getStatusCode().value());
    }

    @Test
    void permissionTable() {
        // 공통 문서는 관리자만, 고객사 문서는 관리자와 그 고객사를 담당하는 엔지니어만 바꾼다
        assertEquals(1, service.create(ADMIN, upload(DocumentFiles.COMMON, "runbook")).get("version"));
        assertEquals(1, service.create(ADMIN, upload("tenant-b", "sla")).get("version"));
        assertEquals(1, service.create(ENGINEER_A, upload("tenant-a", "runbook")).get("version"));
        assertEquals(HttpStatus.FORBIDDEN, status(() -> service.create(ENGINEER_A, upload(DocumentFiles.COMMON, "runbook"))));
        assertEquals(HttpStatus.FORBIDDEN, status(() -> service.create(ENGINEER_A, upload("tenant-b", "runbook"))));
        assertEquals(HttpStatus.FORBIDDEN, status(() -> service.create(VIEWER_A, upload("tenant-a", "runbook"))));

        // 보기: 엔지니어는 공통과 담당 고객사
        assertEquals(true, DocumentService.canView(ENGINEER_A, DocumentFiles.COMMON));
        assertEquals(true, DocumentService.canView(ENGINEER_A, "tenant-a"));
        assertEquals(false, DocumentService.canView(ENGINEER_A, "tenant-b"));
        assertEquals(false, DocumentService.canView(VIEWER_A, "tenant-a"));
        assertEquals(true, DocumentService.canView(ADMIN, "tenant-b"));
        assertEquals(HttpStatus.FORBIDDEN, status(() -> service.list(ENGINEER_A, "tenant-b")));
        assertEquals(HttpStatus.FORBIDDEN, status(() -> service.list(VIEWER_A, null)));
    }

    @Test
    void engineerListSeesCommonAndAssignedTenants() {
        service.list(ENGINEER_A, null);
        verify(documents).list(List.of("tenant-a", DocumentFiles.COMMON));
        service.list(ADMIN, null);
        verify(documents).list(null);
    }

    @Test
    void uploadStoresTheOriginalThenRecordsAPendingVersionAndRequestsIndexing() {
        service.create(ENGINEER_A, upload("tenant-a", "runbook"));
        verify(storage).put(eq("tenant-a/runbook-x/v1/original.md"), eq(MD), anyString());
        ArgumentCaptor<DocumentRepository.Version> version = ArgumentCaptor.forClass(DocumentRepository.Version.class);
        verify(documents).insertVersion(version.capture());
        assertEquals("md", version.getValue().format());
        assertEquals(DocumentService.sha256(MD), version.getValue().sha256());
        ArgumentCaptor<DocumentOutbox.Message> message = ArgumentCaptor.forClass(DocumentOutbox.Message.class);
        verify(index).request(message.capture());
        assertEquals(DocumentOutbox.INDEX, message.getValue().kind());
        assertEquals(version.getValue().id(), message.getValue().documentVersionId());
        verify(audit).record(eq("tenant-a"), eq("eng-a"), eq("DOCUMENT_UPLOADED"), any(), any());
    }

    @Test
    void duplicateDocIdIsAConflictAndUnknownTenantIsRejected() {
        when(documents.find("tenant-a", "runbook-x")).thenReturn(Optional.of(document("tenant-a", null)));
        assertEquals(HttpStatus.CONFLICT, status(() -> service.create(ENGINEER_A, upload("tenant-a", "runbook"))));
        when(tenants.find("tenant-z")).thenReturn(Optional.empty());
        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, status(() -> service.create(ADMIN, upload("tenant-z", "sla"))));
        verify(storage, never()).put(anyString(), any(), anyString());
    }

    private static DocumentRepository.Document document(String tenant, java.time.OffsetDateTime deletedAt) {
        return new DocumentRepository.Document(UUID.randomUUID(), tenant, "runbook-x", "runbook", "런북", 1, deletedAt,
                null, null, "admin", null);
    }

    private static DocumentRepository.Version version(UUID documentId, int number, String format) {
        return new DocumentRepository.Version(UUID.randomUUID(), documentId, number, "runbook-x." + format, format,
                "tenant-a/runbook-x/v%d/original.%s".formatted(number, format), "sha", 10, null, "admin", null,
                "INDEXED", null, null, null, 1, 3, null, false, null);
    }

    @Test
    void newVersionMayChangeTheFormat() {
        var doc = document("tenant-a", null);
        when(documents.get(doc.id())).thenReturn(doc);
        when(documents.latestVersion(doc.id())).thenReturn(2);
        service.newVersion(ENGINEER_A, doc.id(), "runbook-x.pdf", "%PDF-1.4 ...".getBytes(StandardCharsets.US_ASCII));
        verify(storage).put(eq("tenant-a/runbook-x/v3/original.pdf"), any(), eq("application/pdf"));
    }

    @Test
    void restoreCopiesTheChosenVersionAsANewVersion() {
        var doc = document("tenant-a", null);
        when(documents.get(doc.id())).thenReturn(doc);
        when(documents.version(doc.id(), 1)).thenReturn(version(doc.id(), 1, "docx"));
        when(documents.latestVersion(doc.id())).thenReturn(3);
        assertEquals(4, service.restore(ENGINEER_A, doc.id(), 1).get("version"));
        verify(storage).copy("tenant-a/runbook-x/v1/original.docx", "tenant-a/runbook-x/v4/original.docx");
        ArgumentCaptor<DocumentRepository.Version> created = ArgumentCaptor.forClass(DocumentRepository.Version.class);
        verify(documents).insertVersion(created.capture());
        assertEquals(1, created.getValue().restoredFrom());
        verify(index).request(any());
        verify(audit).record(eq("tenant-a"), eq("eng-a"), eq("DOCUMENT_RESTORED"), any(), any());
    }

    @Test
    void deleteMarksTheDocumentAndRequestsChunkRemovalButKeepsTheFiles() {
        var doc = document("tenant-a", null);
        when(documents.get(doc.id())).thenReturn(doc);
        service.delete(ENGINEER_A, doc.id());
        verify(documents).markDeleted(doc.id(), "eng-a");
        verify(index).request(new DocumentOutbox.Message(DocumentOutbox.DELETE, doc.id(), null));
        verify(storage, never()).put(anyString(), any(), anyString());

        var deleted = document("tenant-a", java.time.OffsetDateTime.now());
        when(documents.get(deleted.id())).thenReturn(deleted);
        assertEquals(HttpStatus.CONFLICT, status(() -> service.newVersion(ADMIN, deleted.id(), "a.md", MD)));
    }

    @Test
    void extractedTextOfPdfNeedsASuccessfulIndex() {
        var doc = document("tenant-a", null);
        var pdf = version(doc.id(), 2, "pdf");
        assertEquals(HttpStatus.NOT_FOUND, status(() -> service.extractedText(pdf)));
        when(storage.get("tenant-a/runbook-x/v1/original.md")).thenReturn(MD);
        assertEquals(new String(MD, StandardCharsets.UTF_8), service.extractedText(version(doc.id(), 1, "md")));
    }
}

package com.opslens.backend.document;

import static com.opslens.backend.document.DocumentFiles.COMMON;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.task.TaskTokens;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * rag-mcp의 원문 참조 (SPEC-document-store 7장). 검색 버전의 추출 본문을 준다.
 * 경로의 고객사가 작업 토큰의 고객사가 아니면 403이다. 공통 문서(_common)는 어느 고객사 작업이든 읽는다.
 */
@RestController
public class TenantDocumentController {

    private final DocumentRepository documents;
    private final DocumentService service;
    private final TaskTokens taskTokens;
    private final AuditLog audit;

    public TenantDocumentController(DocumentRepository documents, DocumentService service, TaskTokens taskTokens,
            AuditLog audit) {
        this.documents = documents;
        this.service = service;
        this.taskTokens = taskTokens;
        this.audit = audit;
    }

    @GetMapping(value = "/internal/tenants/{tenantId}/documents/{docId}", produces = "text/markdown;charset=UTF-8")
    ResponseEntity<String> read(@PathVariable String tenantId, @PathVariable String docId,
            @RequestHeader(value = "X-Task-Token", required = false) String taskToken) {
        TaskTokens.Claims claims = taskTokens.verify(taskToken);
        if (!COMMON.equals(tenantId) && !tenantId.equals(claims.tenantId())) {
            audit.record(claims.tenantId(), "agent", "TENANT_ISOLATION_BLOCKED",
                    Map.of("requested", tenantId, "document", docId, "taskId", claims.taskId()), null);
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "다른 고객사의 문서는 읽을 수 없다");
        }
        DocumentRepository.Document document = documents.find(tenantId, docId)
                .filter(d -> !d.deleted() && d.activeVersion() != null)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "문서를 찾을 수 없다: " + docId));
        return ResponseEntity.ok(service.extractedText(documents.version(document.id(), document.activeVersion())));
    }
}

package com.opslens.backend.document;

import com.opslens.backend.auth.ServiceTokenFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * document-indexer가 부르는 내부 API (SPEC-document-store 4.2). INDEXER_BACKEND_TOKEN으로만 통한다(ServiceTokenFilter).
 * 상태의 기준은 DB다. 결과 보고는 그 버전이 아직 대기·실패이고 검색 버전보다 높을 때만 받는다.
 */
@RestController
@RequestMapping("/internal/documents")
public class InternalDocumentController {

    static final Set<String> STAGES = Set.of("PARSE", "CHUNK", "VALIDATE", "EMBED", "STORE");

    record Result(@NotBlank String status, String stage, String error, Boolean retryable, Integer chunks,
            List<String> clauses, String extracted) {}

    private final DocumentRepository documents;
    private final DocumentStorage storage;

    public InternalDocumentController(DocumentRepository documents, DocumentStorage storage) {
        this.documents = documents;
        this.storage = storage;
    }

    /** 메시지로 받은 버전의 처리 정보. 최신 버전이 아니거나 이미 끝난 버전이면 indexer가 건너뛴다 */
    @GetMapping("/versions/{id}")
    Map<String, Object> version(HttpServletRequest request, @PathVariable UUID id) {
        requireIndexer(request);
        DocumentRepository.Version v = documents.versionById(id);
        DocumentRepository.Document d = documents.get(v.documentId());
        Map<String, Object> info = new LinkedHashMap<>();
        info.put("documentVersionId", v.id());
        info.put("documentId", d.id());
        info.put("tenantId", d.tenantId());
        info.put("docId", d.docId());
        info.put("docType", d.docType());
        info.put("title", d.title());
        info.put("version", v.version());
        info.put("format", v.format());
        info.put("sha256", v.sha256());
        info.put("status", v.indexStatus());
        info.put("retryable", v.retryable());
        info.put("latest", documents.latestVersion(d.id()) == v.version());
        info.put("deleted", d.deleted());
        info.put("activeVersion", d.activeVersion());
        // 공통 표준 런북이면 검사 기준(검색 버전의 조항 번호). 검색 버전이 없으면 null(처음 올린 버전이 기준이 된다)
        info.put("referenceClauses", documents.activeClauses(d).orElse(null));
        return info;
    }

    @GetMapping(value = "/versions/{id}/original", produces = MediaType.APPLICATION_OCTET_STREAM_VALUE)
    ResponseEntity<byte[]> original(HttpServletRequest request, @PathVariable UUID id) {
        requireIndexer(request);
        return ResponseEntity.ok(storage.get(documents.versionById(id).objectKey()));
    }

    @PostMapping("/versions/{id}/result")
    @Transactional
    Map<String, Object> result(HttpServletRequest request, @PathVariable UUID id, @Valid @RequestBody Result result) {
        requireIndexer(request);
        DocumentRepository.Version v = documents.versionById(id);
        boolean applied;
        if (DocumentRepository.INDEXED.equals(result.status())) {
            if (result.chunks() == null || result.chunks() < 1) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "INDEXED에는 조각 수가 있어야 한다");
            }
            boolean binary = !v.format().equals("md") && !v.format().equals("txt");
            if (binary && (result.extracted() == null || result.extracted().isBlank())) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "PDF·Word는 추출 본문이 있어야 한다");
            }
            applied = documents.recordIndexed(v, result.chunks(), result.clauses(), binary);
            if (applied && binary) {
                storage.put(DocumentStorage.extractedKey(v.objectKey()),
                        result.extracted().getBytes(StandardCharsets.UTF_8), "text/markdown; charset=utf-8");
            }
        } else if (DocumentRepository.FAILED.equals(result.status())) {
            if (result.stage() == null || !STAGES.contains(result.stage())) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "실패 단계는 " + STAGES + " 중 하나다");
            }
            String error = result.error() == null ? "" : result.error();
            applied = documents.recordFailed(id, result.stage(), error.length() > 500 ? error.substring(0, 500) : error,
                    Boolean.TRUE.equals(result.retryable()));
        } else {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "status는 INDEXED 또는 FAILED다");
        }
        return Map.of("applied", applied);
    }

    @GetMapping("/{documentId}")
    Map<String, Object> document(HttpServletRequest request, @PathVariable UUID documentId) {
        requireIndexer(request);
        DocumentRepository.Document d = documents.get(documentId);
        return Map.of("documentId", d.id(), "tenantId", d.tenantId(), "docId", d.docId(), "deleted", d.deleted());
    }

    @PostMapping("/{documentId}/chunks-removed")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void chunksRemoved(HttpServletRequest request, @PathVariable UUID documentId) {
        requireIndexer(request);
        documents.markChunksRemoved(documentId);
    }

    /** Kafka 없이(DISPATCH_MODE=http) 돌 때 indexer가 가져가는 대기 목록. 메시지와 같은 모양이다 */
    @GetMapping("/pending")
    List<DocumentOutbox.Message> pending(HttpServletRequest request) {
        requireIndexer(request);
        List<DocumentOutbox.Message> messages = new ArrayList<>();
        documents.waiting(0).forEach(v -> messages.add(
                new DocumentOutbox.Message(DocumentOutbox.INDEX, v.documentId(), v.id())));
        documents.deletedWithChunks().forEach(d -> messages.add(
                new DocumentOutbox.Message(DocumentOutbox.DELETE, d.id(), null)));
        return messages;
    }

    /** 경로 표가 이미 indexer 토큰만 통과시키지만, 다른 경로를 이 컨트롤러로 옮겨도 새지 않게 다시 본다 */
    private static void requireIndexer(HttpServletRequest request) {
        if (!"indexer".equals(request.getAttribute(ServiceTokenFilter.CALLER))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "indexer만 부른다");
        }
    }
}

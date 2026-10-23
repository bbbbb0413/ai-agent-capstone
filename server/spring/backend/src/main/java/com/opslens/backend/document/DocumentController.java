package com.opslens.backend.document;

import com.opslens.backend.auth.CurrentUser;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

/** 문서 화면 API (SPEC-document-store 4.1). 권한은 DocumentService가 본다. */
@RestController
@RequestMapping("/api/documents")
public class DocumentController {

    private final DocumentService service;

    public DocumentController(DocumentService service) {
        this.service = service;
    }

    @GetMapping
    List<DocumentRepository.Summary> list(@AuthenticationPrincipal Jwt jwt,
            @RequestParam(required = false) String tenant) {
        return service.list(CurrentUser.from(jwt), tenant);
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    Map<String, Object> create(@AuthenticationPrincipal Jwt jwt, @RequestParam String tenant,
            @RequestParam String docType, @RequestParam String docId, @RequestParam String title,
            @RequestParam MultipartFile file) {
        return service.create(CurrentUser.from(jwt),
                new DocumentService.Upload(tenant, docId, docType, title, file.getOriginalFilename(), bytes(file)));
    }

    @GetMapping("/{id}")
    Map<String, Object> detail(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        return service.detail(CurrentUser.from(jwt), id);
    }

    @PostMapping(value = "/{id}/versions", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    Map<String, Object> newVersion(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id,
            @RequestParam MultipartFile file) {
        return service.newVersion(CurrentUser.from(jwt), id, file.getOriginalFilename(), bytes(file));
    }

    @GetMapping("/{id}/versions/{version}/original")
    ResponseEntity<byte[]> original(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id, @PathVariable int version) {
        DocumentService.FileContent file = service.original(CurrentUser.from(jwt), id, version);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(file.contentType()))
                .header("X-Content-Type-Options", "nosniff").header("Cache-Control", "no-store")
                .header("Content-Disposition", ContentDisposition.attachment()
                        .filename(file.filename(), StandardCharsets.UTF_8).build().toString())
                .body(file.content());
    }

    @GetMapping(value = "/{id}/versions/{version}/extracted", produces = "text/markdown;charset=UTF-8")
    ResponseEntity<String> extracted(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id,
            @PathVariable int version) {
        return ResponseEntity.ok().header("X-Content-Type-Options", "nosniff")
                .body(service.extracted(CurrentUser.from(jwt), id, version));
    }

    @PostMapping("/{id}/versions/{version}/restore")
    @ResponseStatus(HttpStatus.CREATED)
    Map<String, Object> restore(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id, @PathVariable int version) {
        return service.restore(CurrentUser.from(jwt), id, version);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void delete(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        service.delete(CurrentUser.from(jwt), id);
    }

    private static byte[] bytes(MultipartFile file) {
        try {
            return file.getBytes();
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "파일을 읽을 수 없다");
        }
    }
}

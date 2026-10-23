package com.opslens.backend.document;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.opslens.backend.auth.ServiceTokenFilter;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.server.ResponseStatusException;

/** indexer 결과 보고: 상태의 기준은 DB이고, 지난 버전이 최신을 덮지 않는다 (SPEC-document-store 2장) */
class InternalDocumentControllerTest {

    private final DocumentRepository documents = mock(DocumentRepository.class);
    private final DocumentStorage storage = mock(DocumentStorage.class);
    private final InternalDocumentController controller = new InternalDocumentController(documents, storage);

    private static MockHttpServletRequest caller(String name) {
        var request = new MockHttpServletRequest();
        request.setAttribute(ServiceTokenFilter.CALLER, name);
        return request;
    }

    private DocumentRepository.Version version(String format) {
        var v = new DocumentRepository.Version(UUID.randomUUID(), UUID.randomUUID(), 2, "a." + format, format,
                "tenant-a/sla-a/v2/original." + format, "sha", 10, null, "eng", null, "PENDING", null, null, null, 0,
                null, null, false, null);
        when(documents.versionById(v.id())).thenReturn(v);
        return v;
    }

    @Test
    void indexedPdfStoresTheExtractedTextOnlyWhenApplied() {
        var v = version("pdf");
        when(documents.recordIndexed(v, 5, null, true)).thenReturn(true);
        var result = controller.result(caller("indexer"), v.id(),
                new InternalDocumentController.Result("INDEXED", null, null, null, 5, null, "# 계약서\n본문"));
        assertEquals(true, result.get("applied"));
        verify(storage).put(eq("tenant-a/sla-a/v2/extracted.md"), any(), anyString());

        // 검색 버전이 이미 더 높으면(늦게 끝난 지난 버전) 받지 않고 추출 본문도 쓰지 않는다
        var late = version("docx");
        when(documents.recordIndexed(late, 5, null, true)).thenReturn(false);
        var ignored = controller.result(caller("indexer"), late.id(),
                new InternalDocumentController.Result("INDEXED", null, null, null, 5, null, "본문"));
        assertEquals(false, ignored.get("applied"));
        verify(storage, times(1)).put(anyString(), any(), anyString());   // 앞의 PDF 한 번뿐이다
    }

    @Test
    void indexedMarkdownNeedsNoExtractedText() {
        var v = version("md");
        when(documents.recordIndexed(v, 3, List.of("RB-STD-01"), false)).thenReturn(true);
        controller.result(caller("indexer"), v.id(),
                new InternalDocumentController.Result("INDEXED", null, null, null, 3, List.of("RB-STD-01"), null));
        verify(storage, never()).put(anyString(), any(), anyString());
    }

    @Test
    void badReportsAreRejected() {
        var pdf = version("pdf");
        for (var bad : List.of(
                new InternalDocumentController.Result("INDEXED", null, null, null, 0, null, "x"),     // 조각 0개
                new InternalDocumentController.Result("INDEXED", null, null, null, 3, null, null),    // PDF 추출 본문 없음
                new InternalDocumentController.Result("FAILED", "UPLOAD", "x", false, null, null, null),
                new InternalDocumentController.Result("DONE", null, null, null, 1, null, null))) {
            var e = assertThrows(ResponseStatusException.class, () -> controller.result(caller("indexer"), pdf.id(), bad));
            assertEquals(HttpStatus.BAD_REQUEST, e.getStatusCode());
        }
    }

    @Test
    void failureKeepsAShortReasonAndRetryFlag() {
        var v = version("pdf");
        when(documents.recordFailed(eq(v.id()), eq("EMBED"), anyString(), eq(true))).thenReturn(true);
        var result = controller.result(caller("indexer"), v.id(),
                new InternalDocumentController.Result("FAILED", "EMBED", "x".repeat(900), true, null, null, null));
        assertEquals(true, result.get("applied"));
        verify(documents).recordFailed(v.id(), "EMBED", "x".repeat(500), true);
    }

    @Test
    void onlyTheIndexerCalls() {
        var v = version("md");
        var e = assertThrows(ResponseStatusException.class, () -> controller.version(caller("mcp"), v.id()));
        assertEquals(HttpStatus.FORBIDDEN, e.getStatusCode());
    }
}

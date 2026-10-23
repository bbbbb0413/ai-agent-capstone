package com.opslens.backend.document;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

/** 업로드 때 backend가 하는 검사: 형식·크기·이름 (SPEC-document-store 4.1) */
class DocumentFilesTest {

    private static final byte[] PDF = "%PDF-1.7\n...".getBytes(StandardCharsets.US_ASCII);
    private static final byte[] DOCX = {0x50, 0x4b, 0x03, 0x04, 0x14, 0x00};

    private static String reason(Runnable call) {
        ResponseStatusException e = assertThrows(ResponseStatusException.class, call::run);
        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, e.getStatusCode());
        return e.getReason();
    }

    @Test
    void formatComesFromTheExtensionAndMustMatchTheContent() {
        assertEquals("pdf", DocumentFiles.check("SLA 계약서.PDF", PDF).format());
        assertEquals("docx", DocumentFiles.check("runbook.docx", DOCX).format());
        assertEquals("md", DocumentFiles.check("dir/../runbook.md", "# 제목\n".getBytes(StandardCharsets.UTF_8)).format());
        assertEquals("runbook.md", DocumentFiles.check("a\\b/runbook.md", "x".getBytes(StandardCharsets.UTF_8)).filename());

        // 확장자만 바꾼 파일
        assertTrue(reason(() -> DocumentFiles.check("contract.pdf", DOCX)).contains("pdf 형식이 아니다"));
        assertTrue(reason(() -> DocumentFiles.check("runbook.docx", PDF)).contains("docx 형식이 아니다"));
        assertTrue(reason(() -> DocumentFiles.check("notes.md", new byte[] {'#', ' ', 0x00, 'x'})).contains("UTF-8"));
        assertTrue(reason(() -> DocumentFiles.check("binary.txt", new byte[] {(byte) 0xff, (byte) 0xfe, 0x00}))
                .contains("UTF-8"));
        assertTrue(reason(() -> DocumentFiles.check("slides.pptx", DOCX)).contains("md, txt, pdf, docx"));
        assertTrue(reason(() -> DocumentFiles.check("noext", PDF)).contains("md, txt, pdf, docx"));
        assertTrue(reason(() -> DocumentFiles.check("empty.md", new byte[0])).contains("빈 파일"));
    }

    @Test
    void sizeLimitDependsOnTheFormat() {
        byte[] text = "a".repeat((1 << 20) + 1).getBytes(StandardCharsets.UTF_8);
        assertTrue(reason(() -> DocumentFiles.check("big.md", text)).contains("1MB"));
        byte[] pdf = new byte[(20 << 20) + 1];
        System.arraycopy(PDF, 0, pdf, 0, PDF.length);
        assertTrue(reason(() -> DocumentFiles.check("big.pdf", pdf)).contains("20MB"));
        byte[] okPdf = new byte[5 << 20];
        System.arraycopy(PDF, 0, okPdf, 0, PDF.length);
        assertEquals("pdf", DocumentFiles.check("ok.pdf", okPdf).format());   // 텍스트 상한(1MB)보다 큰 PDF도 된다
    }

    @Test
    void typeDependsOnTheScope() {
        DocumentFiles.checkType("tenant-a", "sla");
        DocumentFiles.checkType("tenant-a", "runbook");
        DocumentFiles.checkType(DocumentFiles.COMMON, "runbook");
        reason(() -> DocumentFiles.checkType(DocumentFiles.COMMON, "sla"));       // 공통 문서는 표준 런북뿐이다
        reason(() -> DocumentFiles.checkType("tenant-a", "postmortem"));          // 회고는 올리지 않는다
    }

    @Test
    void docIdAndTitleRules() {
        DocumentFiles.checkDocId("runbook-order-api");
        for (String bad : new String[] {null, "", "a", "Runbook", "run_book", "-lead", "a/b", "x".repeat(64)}) {
            reason(() -> DocumentFiles.checkDocId(bad));
        }
        assertEquals("B사 SLA 계약서", DocumentFiles.checkTitle("  B사 SLA 계약서 "));
        reason(() -> DocumentFiles.checkTitle(" "));
        reason(() -> DocumentFiles.checkTitle("두\n줄"));
    }
}

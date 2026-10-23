package com.opslens.backend.document;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 처음 데이터: 저장소의 data/documents에서 종류·제목을 정하고, DB에 없는 문서만 올린다 (SPEC-document-store 6장) */
class DocumentSeederTest {

    @TempDir
    Path root;

    @Test
    void seedFilesTakeTypeFromThePrefixAndTitleFromTheFirstHeading() throws Exception {
        Files.createDirectories(root.resolve("_common"));
        Files.createDirectories(root.resolve("tenant-a"));
        Files.writeString(root.resolve("_common/runbook-standard.md"), "머리말\n# MSP 표준 런북\n## RB-STD-01\n");
        Files.writeString(root.resolve("tenant-a/sla-contract.md"), "# A사 SLA 계약서\n");
        Files.writeString(root.resolve("tenant-a/postmortem-incident-1.md"), "# 회고\n");   // 회고는 시드가 아니다
        Files.writeString(root.resolve("tenant-a/notes.txt"), "x");

        var files = DocumentSeeder.files(root);
        assertEquals(2, files.size());
        assertEquals(new DocumentSeeder.SeedFile("_common", "runbook-standard", "runbook", "MSP 표준 런북",
                root.resolve("_common/runbook-standard.md")), files.get(0));
        assertEquals("sla", files.get(1).docType());
        assertEquals("A사 SLA 계약서", files.get(1).title());
    }

    @Test
    void seedingContinuesWhenOneDocumentFails() throws Exception {
        Files.createDirectories(root.resolve("tenant-a"));
        Files.writeString(root.resolve("tenant-a/runbook-a.md"), "# A\n");
        Files.writeString(root.resolve("tenant-a/sla-a.md"), "# S\n");
        DocumentService service = mock(DocumentService.class);
        when(service.seed(eq("tenant-a"), eq("runbook-a"), anyString(), anyString(), anyString(), any()))
                .thenThrow(new IllegalStateException("storage not ready"));
        new DocumentSeeder(service, new DocumentStoreProperties(null, null, null, null, root.toString())).seed();
        verify(service).seed(eq("tenant-a"), eq("sla-a"), eq("sla"), eq("S"), eq("sla-a.md"), any());
    }
}

package com.opslens.backend.attachment;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.*;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.server.ResponseStatusException;
import com.opslens.backend.task.*;

class TaskAttachmentsTest {
    MockMultipartFile file(String name, byte[] bytes) { return new MockMultipartFile("attachments",name,"application/octet-stream",bytes); }
    @Test void readsUtf8AndUsesSafeBasename() {
        var prepared=TaskAttachments.validate(List.of(file("C:\\temp\\오류.log","ERROR 연결 실패".getBytes(java.nio.charset.StandardCharsets.UTF_8)))).getFirst();
        assertEquals("오류.log",prepared.filename()); assertEquals("text/plain",prepared.mediaType());
    }
    @Test void detectsImageBytesInsteadOfTrustingBrowserMime() throws Exception {
        var bytes=new ByteArrayOutputStream();ImageIO.write(new BufferedImage(20,20,BufferedImage.TYPE_INT_RGB),"png",bytes);
        assertEquals("image/png",TaskAttachments.validate(List.of(file("error.png",bytes.toByteArray()))).getFirst().mediaType());
        assertThrows(ResponseStatusException.class,()->TaskAttachments.validate(List.of(file("fake.png","<svg>danger</svg>".getBytes()))));
    }
    @Test void rejectsBinaryEmptyInvalidUtf8AndUnsupportedFiles() {
        for(var f: List.of(file("data.log",new byte[]{0,1,2}),file("bad.txt",new byte[]{(byte)0xff}),file("empty.txt",new byte[0]),file("x.html","hello".getBytes())))
            assertThrows(ResponseStatusException.class,()->TaskAttachments.validate(List.of(f)));
    }
    @Test void rejectsSizeAndCountBeforeSaving() {
        var small=file("x.txt","test".getBytes());
        assertThrows(ResponseStatusException.class,()->TaskAttachments.validate(List.of(small,small,small,small)));
        assertThrows(ResponseStatusException.class,()->TaskAttachments.validate(List.of(file("huge.log",new byte[512*1024+1]))));
        assertThrows(ResponseStatusException.class,()->TaskAttachments.validate(List.of(file("huge.png",new byte[4*1024*1024+1]))));
    }
    Jwt user(String role,String tenant) { return Jwt.withTokenValue("test").header("alg","HS256").subject("engineer").claim("uid",2L).claim("role",role).claim("tenants",List.of(tenant)).build(); }
    @Test void tenantAndStaffGuardsApplyBeforeReadingOrCreatingFiles() {
        var tasks=mock(TaskRepository.class);var files=mock(TaskAttachments.class);var service=mock(TaskService.class);
        var controller=new AttachmentController(files,service,tasks);var id=UUID.randomUUID();
        var task=mock(TaskRecord.class);when(task.tenantId()).thenReturn("tenant-a");when(tasks.get(id)).thenReturn(task);
        assertThrows(ResponseStatusException.class,()->controller.list(id,user("MSP_ENGINEER","tenant-b")));
        assertThrows(ResponseStatusException.class,()->controller.list(id,user("TENANT_VIEWER","tenant-a")));
        assertThrows(ResponseStatusException.class,()->controller.internal(id,"tenant-b"));
        assertThrows(ResponseStatusException.class,()->controller.create(user("TENANT_VIEWER","tenant-a"),"tenant-a","test",List.of(file("x.txt","test".getBytes()))));
        verifyNoInteractions(files,service);
    }
    @Test void authorizedReadReturnsMetadataAndDownloadIsNotExecutable() {
        var tasks=mock(TaskRepository.class);var files=mock(TaskAttachments.class);var id=UUID.randomUUID();var fileId=UUID.randomUUID();
        var task=mock(TaskRecord.class);when(task.tenantId()).thenReturn("tenant-a");when(tasks.get(id)).thenReturn(task);
        when(files.get(id,fileId)).thenReturn(new TaskAttachments.Content(fileId,"test.log","text/plain","ERROR".getBytes()));
        var response=new AttachmentController(files,mock(TaskService.class),tasks).download(id,fileId,user("MSP_ENGINEER","tenant-a"));
        assertEquals("nosniff",response.getHeaders().getFirst("X-Content-Type-Options"));
        assertTrue(response.getHeaders().getFirst("Content-Disposition").startsWith("attachment;"));
        assertEquals("no-store",response.getHeaders().getFirst("Cache-Control"));
    }

    @Test void multipartControllerBindsFilesAndUsesDedicatedAnalysisType() throws Exception {
        var service=mock(TaskService.class);var id=UUID.randomUUID();
        when(service.create(eq("tenant-a"),isNull(),eq("ATTACHMENT"),anyString(),eq(2L),anyList())).thenReturn(id);
        var mvc=org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup(
            new AttachmentController(mock(TaskAttachments.class),service,mock(TaskRepository.class)))
            .setCustomArgumentResolvers(new org.springframework.security.web.method.annotation.AuthenticationPrincipalArgumentResolver()).build();
        org.springframework.security.core.context.SecurityContextHolder.getContext().setAuthentication(
            new org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken(user("MSP_ENGINEER","tenant-a")));
        try {
            mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart("/api/ai/tasks/with-attachments")
                .file(file("sample.log","ERROR timeout".getBytes())).param("tenantId","tenant-a"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isAccepted())
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath("$.taskId").value(id.toString()));
            verify(service).create(eq("tenant-a"),isNull(),eq("ATTACHMENT"),contains("첨부한 로그"),eq(2L),argThat(f->f.size()==1));
        } finally { org.springframework.security.core.context.SecurityContextHolder.clearContext(); }
    }
}

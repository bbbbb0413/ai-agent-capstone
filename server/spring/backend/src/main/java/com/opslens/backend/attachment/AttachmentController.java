package com.opslens.backend.attachment;

import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.task.TaskRepository;
import com.opslens.backend.task.TaskService;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.http.*;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class AttachmentController {
    private final TaskAttachments files;
    private final TaskService service;
    private final TaskRepository tasks;
    public AttachmentController(TaskAttachments files, TaskService service, TaskRepository tasks) {
        this.files=files;this.service=service;this.tasks=tasks;
    }
    @PostMapping(value="/api/ai/tasks/with-attachments", consumes=MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public Map<String,UUID> create(@AuthenticationPrincipal Jwt jwt, @RequestParam String tenantId,
            @RequestParam(defaultValue="") String question, @RequestParam List<MultipartFile> attachments) {
        CurrentUser user=CurrentUser.from(jwt);
        user.requireMspStaff(); user.requireAccess(tenantId);
        if (question.length()>4000) throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"질문은 4,000자 이내로 입력하세요");
        var prepared=TaskAttachments.validate(attachments);
        String prompt=question.isBlank()?"첨부한 로그와 에러 화면을 분석하고 원인 후보와 확인 방법을 알려줘":question.trim();
        return Map.of("taskId",service.create(tenantId,null,"ATTACHMENT",prompt,user.id(),prepared));
    }
    private void access(UUID task, Jwt jwt) {
        CurrentUser user=CurrentUser.from(jwt);user.requireMspStaff();user.requireAccess(tasks.get(task).tenantId());
    }
    @GetMapping("/api/ai/tasks/{task}/attachments")
    public List<TaskAttachments.Info> list(@PathVariable UUID task,@AuthenticationPrincipal Jwt jwt) {
        access(task,jwt);return files.list(task);
    }
    @GetMapping("/api/ai/tasks/{task}/attachments/{id}")
    public ResponseEntity<byte[]> download(@PathVariable UUID task,@PathVariable UUID id,@AuthenticationPrincipal Jwt jwt) {
        access(task,jwt);var file=files.get(task,id);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(file.mediaType()))
            .header("X-Content-Type-Options","nosniff").header("Cache-Control","no-store")
            .header("Content-Disposition",ContentDisposition.attachment().filename(file.filename(),StandardCharsets.UTF_8).build().toString())
            .body(file.content());
    }
    // ServiceTokenFilter + 현재 작업의 tenant 검사. 파일 URL/외부 주소는 받지 않는다.
    @GetMapping("/internal/tasks/{task}/attachments")
    public List<TaskAttachments.Content> internal(@PathVariable UUID task,@RequestHeader("X-Tenant-Id") String tenant) {
        if (!tasks.get(task).tenantId().equals(tenant)) throw new ResponseStatusException(HttpStatus.FORBIDDEN,"고객사가 다릅니다");
        return files.contents(task);
    }
}

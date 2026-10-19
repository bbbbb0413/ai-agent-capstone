package com.opslens.backend.task;

import com.opslens.backend.auth.CurrentUser;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/** 화면이 호출하는 AI 작업 API 5개 (기획안 5.1, PDF 표 5-8). */
@RestController
@RequestMapping("/api/ai/tasks")
public class TaskController {

    record CreateTaskRequest(@NotBlank String tenantId, Long incidentId, @NotBlank String question) {}

    record ApprovalRequest(@Pattern(regexp = "APPROVE|REJECT") String decision, String comment) {}

    private final TaskService service;
    private final TaskRepository tasks;
    private final TaskEventHub events;

    public TaskController(TaskService service, TaskRepository tasks, TaskEventHub events) {
        this.service = service;
        this.tasks = tasks;
        this.events = events;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, UUID> create(@AuthenticationPrincipal Jwt jwt, @Valid @RequestBody CreateTaskRequest request) {
        CurrentUser user = CurrentUser.from(jwt);
        user.requireAccess(request.tenantId());
        user.requireMspStaff();
        UUID taskId = service.create(request.tenantId(), request.incidentId(), "QUESTION", request.question(),
                user.id());
        return Map.of("taskId", taskId);
    }

    @GetMapping("/{id}")
    TaskRecord get(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        TaskRecord task = tasks.get(id);
        CurrentUser.from(jwt).requireAccess(task.tenantId());
        return task;
    }

    @GetMapping(path = "/{id}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    SseEmitter stream(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        CurrentUser.from(jwt).requireAccess(tasks.get(id).tenantId());
        return events.subscribe(id);
    }

    @PostMapping("/{id}/approval")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void approve(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id,
            @Valid @RequestBody ApprovalRequest request) {
        service.approve(id, CurrentUser.from(jwt), request.decision(), request.comment());
    }

    @GetMapping
    List<TaskRecord> list(@AuthenticationPrincipal Jwt jwt, @RequestParam(defaultValue = "false") boolean mine) {
        CurrentUser user = CurrentUser.from(jwt);
        return mine ? tasks.listByRequester(user.id()) : tasks.listByTenants(user.tenants());
    }
}

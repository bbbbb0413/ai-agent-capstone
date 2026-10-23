package com.opslens.backend.task;

import com.opslens.backend.auth.CurrentUser;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 작업 관리 (MSP 관리자). DLT로 간 작업은 메시지를 다시 넣지 않고 여기서 새 작업으로 다시 실행한다. */
@RestController
@RequestMapping("/api/admin/tasks")
public class TaskAdminController {

    private final TaskService service;

    public TaskAdminController(TaskService service) {
        this.service = service;
    }

    @PostMapping("/{id}/retry")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, UUID> retry(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        return Map.of("taskId", service.retry(id, CurrentUser.from(jwt)));
    }
}

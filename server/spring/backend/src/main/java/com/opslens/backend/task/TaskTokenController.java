package com.opslens.backend.task;

import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** 작업 토큰 발급(agent)과 검증용 공개키(MCP Server). 서비스 토큰은 ServiceTokenFilter가 경로별로 확인한다. */
@RestController
public class TaskTokenController {

    private final TaskRepository tasks;
    private final TaskTokens tokens;

    public TaskTokenController(TaskRepository tasks, TaskTokens tokens) {
        this.tasks = tasks;
        this.tokens = tokens;
    }

    /** QUEUED·RUNNING 작업에만 발급한다. 끝났거나 사람을 기다리는 작업은 409 */
    @PostMapping("/internal/tasks/{id}/token")
    TaskTokens.Issued issue(@PathVariable UUID id) {
        return tokens.issue(tasks.get(id));
    }

    @GetMapping("/internal/.well-known/jwks.json")
    Map<String, Object> jwks() {
        return tokens.jwks();
    }
}

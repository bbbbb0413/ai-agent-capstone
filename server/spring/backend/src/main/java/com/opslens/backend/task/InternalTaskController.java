package com.opslens.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** agent → backend. 진행 이벤트와 최종 결과를 받는다. */
@RestController
@RequestMapping("/internal/tasks/{id}")
public class InternalTaskController {

    record TaskEvent(@NotBlank String type, JsonNode data) {}

    private final TaskService service;

    public InternalTaskController(TaskService service) {
        this.service = service;
    }

    @PostMapping("/events")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void event(@PathVariable UUID id, @Valid @RequestBody TaskEvent event) {
        service.onEvent(id, event.type(), event.data());
    }

    @PostMapping("/result")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void result(@PathVariable UUID id, @RequestBody TaskService.TaskResult result) {
        service.complete(id, result);
    }
}

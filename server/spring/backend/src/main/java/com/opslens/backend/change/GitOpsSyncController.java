package com.opslens.backend.change;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.task.TaskRecord;
import com.opslens.backend.task.TaskRepository;
import com.opslens.backend.task.TaskService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * ArgoCD Notifications → backend. 동기화가 끝나고 Healthy가 되면 알림이 온다 (gitops/cluster/argocd-notifications.yaml).
 * 알림의 Application 이름이 고객사 ID다. 그 고객사에서 PR 병합을 기다리던 작업 중 PR이 병합된 것만 재개한다.
 * 기한(opslens.gitops.merge-timeout) 안에 병합되지 않은 작업은 TIMEOUT으로 재개해 에스컬레이션하게 한다.
 */
@RestController
@RequestMapping("/internal/gitops")
public class GitOpsSyncController {

    record SyncEvent(@NotBlank String app, @NotBlank String revision, String health) {}

    private static final Logger log = LoggerFactory.getLogger(GitOpsSyncController.class);

    private final TaskRepository tasks;
    private final TaskService taskService;
    private final GitOpsRepository gitops;
    private final AuditLog audit;
    private final OpsLensProperties.Gitops props;

    public GitOpsSyncController(TaskRepository tasks, TaskService taskService, GitOpsRepository gitops,
            AuditLog audit, OpsLensProperties props) {
        this.tasks = tasks;
        this.taskService = taskService;
        this.gitops = gitops;
        this.audit = audit;
        this.props = props.gitops();
    }

    @PostMapping("/sync-events")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, Object> syncEvent(@Valid @RequestBody SyncEvent event) {
        audit.record(event.app(), "argocd", "GITOPS_SYNCED", event, null);
        List<String> resumed = new ArrayList<>();
        for (TaskRecord task : tasks.listChangePending(event.app())) {
            int number = task.pendingAction().path("prNumber").asInt();
            JsonNode pr;
            try {
                pr = gitops.pullRequest(number);
            } catch (ResponseStatusException e) {
                log.warn("task {}: PR #{} 상태를 확인하지 못했다: {}", task.id(), number, e.getReason());
                continue;
            }
            if (pr.path("merged").asBoolean()
                    && taskService.finishChange(task, "SYNCED", "PR #%d 병합, %s 동기화 완료"
                            .formatted(number, event.revision().substring(0, Math.min(7, event.revision().length()))),
                            "argocd")) {
                resumed.add(task.id().toString());
            }
        }
        return Map.of("resumed", resumed);
    }

    @Scheduled(fixedDelay = 60_000, initialDelay = 60_000)
    void escalateStaleChanges() {
        OffsetDateTime before = OffsetDateTime.now().minus(props.mergeTimeout());
        for (TaskRecord task : tasks.listChangePendingSince(before)) {
            taskService.finishChange(task, "TIMEOUT",
                    "PR이 %d분 안에 병합·동기화되지 않았다".formatted(props.mergeTimeout().toMinutes()), "opslens");
        }
    }
}

package com.opslens.backend.alert;

import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.task.TaskService;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Alertmanager webhook. 고객사를 식별해 인시던트와 진단 작업을 만든다.
 * 같은 서비스에 열린 인시던트가 있으면 알림을 거기에 덧붙이고 새 작업을 만들지 않는다 (SPEC-compound-faults 5장).
 * 같은 알림이 반복해서 오면 아무것도 하지 않는다.
 */
@RestController
public class AlertWebhookController {

    record AlertPayload(List<Alert> alerts) {}

    record Alert(String status, Map<String, String> labels, Map<String, String> annotations, String fingerprint) {}

    private final IncidentRepository incidents;
    private final TaskService tasks;

    public AlertWebhookController(IncidentRepository incidents, TaskService tasks) {
        this.incidents = incidents;
        this.tasks = tasks;
    }

    @PostMapping("/internal/alerts")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void receive(@RequestBody AlertPayload payload) {
        for (Alert alert : payload.alerts()) {
            String tenantId = alert.labels().get("tenant");
            if (tenantId == null) {
                continue;
            }
            if ("resolved".equals(alert.status())) {
                incidents.resolveByFingerprint(alert.fingerprint());
                continue;
            }
            String service = alert.labels().get("service");
            String title = alert.annotations().getOrDefault("summary", alert.labels().get("alertname"));
            String severity = "critical".equals(alert.labels().get("severity")) ? "P1" : "P2";
            IncidentRepository.AlertOutcome outcome = incidents.openOrAttach(tenantId, service,
                    alert.labels().get("alertname"), title, severity, alert.fingerprint());
            if (outcome.opened()) {
                tasks.create(tenantId, outcome.incidentId(), "DIAGNOSE", title, null);
            } else if (outcome.attached()) {
                tasks.alertAdded(outcome.incidentId(), title, severity);
            }
        }
    }
}

package com.opslens.backend.alert;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.opslens.backend.incident.IncidentRepository;
import com.opslens.backend.incident.IncidentRepository.AlertOutcome;
import com.opslens.backend.task.TaskService;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** 같은 서비스의 알림은 인시던트·작업 하나로 묶는다 (SPEC-compound-faults 5장). */
class AlertWebhookControllerTest {

    private final IncidentRepository incidents = mock(IncidentRepository.class);
    private final TaskService tasks = mock(TaskService.class);
    private final AlertWebhookController controller = new AlertWebhookController(incidents, tasks);

    private static AlertWebhookController.Alert firing(String alertname, String severity, String fingerprint) {
        return new AlertWebhookController.Alert("firing",
                Map.of("tenant", "tenant-a", "service", "order-api", "alertname", alertname, "severity", severity),
                Map.of("summary", "tenant-a order-api " + alertname), fingerprint);
    }

    private void receive(AlertWebhookController.Alert... alerts) {
        controller.receive(new AlertWebhookController.AlertPayload(List.of(alerts)));
    }

    @Test
    void firstAlertOpensAnIncidentAndATask() {
        when(incidents.openOrAttach("tenant-a", "order-api", "HighErrorRate", "tenant-a order-api HighErrorRate", "P1",
                "fp-error")).thenReturn(new AlertOutcome(7, true, false));
        receive(firing("HighErrorRate", "critical", "fp-error"));
        verify(tasks).create("tenant-a", 7L, "DIAGNOSE", "tenant-a order-api HighErrorRate", null);
        verify(tasks, never()).alertAdded(anyLong(), anyString(), anyString());
    }

    @Test
    void secondAlertOfTheSameServiceIsAttachedWithoutANewTask() {
        when(incidents.openOrAttach(any(), any(), any(), any(), any(), any())).thenReturn(new AlertOutcome(7, false, true));
        receive(firing("HighLatency", "warning", "fp-latency"));
        verify(tasks).alertAdded(7L, "tenant-a order-api HighLatency", "P2");
        verify(tasks, never()).create(any(), any(), any(), any(), any());
    }

    @Test
    void repeatedAlertDoesNothing() {
        when(incidents.openOrAttach(any(), any(), any(), any(), any(), any())).thenReturn(new AlertOutcome(7, false, false));
        receive(firing("HighErrorRate", "critical", "fp-error"));
        verify(tasks, never()).create(any(), any(), any(), any(), any());
        verify(tasks, never()).alertAdded(anyLong(), anyString(), anyString());
    }

    @Test
    void resolvedAlertGoesToTheRepository() {
        receive(new AlertWebhookController.Alert("resolved", Map.of("tenant", "tenant-a"), Map.of(), "fp-error"));
        verify(incidents).resolveByFingerprint("fp-error");
    }
}

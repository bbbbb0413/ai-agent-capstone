package com.opslens.backend.incident;

import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.escalation.Escalation;
import com.opslens.backend.escalation.EscalationService;
import java.util.List;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/incidents")
public class IncidentController {

    private final IncidentRepository incidents;
    private final EscalationService escalations;
    private final AuditLog audit;

    public IncidentController(IncidentRepository incidents, EscalationService escalations, AuditLog audit) {
        this.incidents = incidents;
        this.escalations = escalations;
        this.audit = audit;
    }

    @GetMapping
    List<Incident> list(@AuthenticationPrincipal Jwt jwt, @RequestParam(required = false) String status) {
        return incidents.list(CurrentUser.from(jwt).tenants(), status);
    }

    @GetMapping("/{id}")
    Incident get(@AuthenticationPrincipal Jwt jwt, @PathVariable long id) {
        Incident incident = incidents.get(id);
        CurrentUser.from(jwt).requireAccess(incident.tenantId());
        return incident;
    }

    @GetMapping("/{id}/notices")
    List<Notice> notices(@AuthenticationPrincipal Jwt jwt, @PathVariable long id) {
        Incident incident = incidents.get(id);
        CurrentUser.from(jwt).requireAccess(incident.tenantId());
        return incidents.notices(incident.tenantId(), id);
    }

    /** 발송 대기열의 에스컬레이션. 내부 호출 기록이라 MSP 직원만 본다 (고객사 포털에는 공지만 보인다) */
    @GetMapping("/{id}/escalations")
    List<Escalation> escalations(@AuthenticationPrincipal Jwt jwt, @PathVariable long id) {
        CurrentUser user = CurrentUser.from(jwt);
        user.requireMspStaff();
        Incident incident = incidents.get(id);
        user.requireAccess(incident.tenantId());
        return escalations.list(incident.tenantId(), id);
    }

    /** 자동 알림이 아닌 사람 제보 인시던트도 복구 확인 뒤 명시적으로 종료한다. */
    @PostMapping("/{id}/resolve")
    Incident resolve(@AuthenticationPrincipal Jwt jwt, @PathVariable long id) {
        CurrentUser user = CurrentUser.from(jwt);
        user.requireAdmin();
        Incident incident = incidents.get(id);
        user.requireAccess(incident.tenantId());
        if (incident.status().equals("RESOLVED")) {
            return incident;
        }
        incidents.resolveManually(incident.tenantId(), id);
        audit.record(incident.tenantId(), user.username(), "INCIDENT_RESOLVED_MANUALLY",
                java.util.Map.of("incidentId", id), null);
        return incidents.get(id);
    }
}

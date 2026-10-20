package com.opslens.backend.incident;

import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.escalation.Escalation;
import com.opslens.backend.escalation.EscalationService;
import java.util.List;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/incidents")
public class IncidentController {

    private final IncidentRepository incidents;
    private final EscalationService escalations;

    public IncidentController(IncidentRepository incidents, EscalationService escalations) {
        this.incidents = incidents;
        this.escalations = escalations;
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
}

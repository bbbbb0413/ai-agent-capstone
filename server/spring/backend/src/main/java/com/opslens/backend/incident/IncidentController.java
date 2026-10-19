package com.opslens.backend.incident;

import com.opslens.backend.auth.CurrentUser;
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

    public IncidentController(IncidentRepository incidents) {
        this.incidents = incidents;
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
}

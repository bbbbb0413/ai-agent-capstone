package com.opslens.backend.impact;

import com.opslens.backend.auth.CurrentUser;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/tenants/{tenantId}/impact")
public class ImpactController {
    private final ImpactService service;
    public ImpactController(ImpactService service) { this.service = service; }
    @GetMapping
    public ImpactService.View get(@PathVariable String tenantId, @AuthenticationPrincipal Jwt jwt) {
        return service.view(tenantId, CurrentUser.from(jwt));
    }
}

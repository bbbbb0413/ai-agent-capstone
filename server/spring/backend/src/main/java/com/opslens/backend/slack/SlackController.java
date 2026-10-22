package com.opslens.backend.slack;

import com.opslens.backend.auth.CurrentUser;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class SlackController {
    private final SlackConnectionService connections;
    private final SlackDeliveryService deliveries;

    public SlackController(SlackConnectionService connections, SlackDeliveryService deliveries) {
        this.connections = connections;
        this.deliveries = deliveries;
    }

    @GetMapping("/tenants/{tenant}/slack")
    SlackConnectionService.View settings(@AuthenticationPrincipal Jwt jwt, @PathVariable String tenant) {
        return connections.view(tenant, CurrentUser.from(jwt));
    }

    @PutMapping("/tenants/{tenant}/slack")
    SlackConnectionService.View save(@AuthenticationPrincipal Jwt jwt, @PathVariable String tenant,
            @RequestBody SlackConnectionService.Update update) {
        return connections.save(tenant, update, CurrentUser.from(jwt));
    }

    @GetMapping("/incidents/{incidentId}/slack-deliveries")
    List<SlackDeliveryService.Delivery> list(@AuthenticationPrincipal Jwt jwt, @PathVariable long incidentId) {
        return deliveries.list(incidentId, CurrentUser.from(jwt));
    }

    @GetMapping("/incidents/{incidentId}/escalations/{escalationId}/slack-preview")
    SlackDeliveryService.Preview preview(@AuthenticationPrincipal Jwt jwt, @PathVariable long incidentId,
            @PathVariable long escalationId) {
        return deliveries.preview(incidentId, escalationId, CurrentUser.from(jwt));
    }

    @PostMapping("/incidents/{incidentId}/escalations/{escalationId}/slack-delivery")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void approve(@AuthenticationPrincipal Jwt jwt, @PathVariable long incidentId, @PathVariable long escalationId,
            @RequestBody SlackDeliveryService.Approval approval) {
        deliveries.approve(incidentId, escalationId, approval, CurrentUser.from(jwt));
    }
}

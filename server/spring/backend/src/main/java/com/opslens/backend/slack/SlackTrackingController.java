package com.opslens.backend.slack;

import com.opslens.backend.auth.CurrentUser;
import java.util.List;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
public class SlackTrackingController {
    public record Action(String action) {}
    private final SlackTrackingSettings settings;
    private final SlackTrackingService tracking;
    public SlackTrackingController(SlackTrackingSettings settings,SlackTrackingService tracking) {
        this.settings=settings;this.tracking=tracking;
    }
    @GetMapping("/api/tenants/{tenant}/slack-tracking")
    public SlackTrackingSettings.View settings(@PathVariable String tenant,@AuthenticationPrincipal Jwt jwt) {
        return settings.view(tenant,CurrentUser.from(jwt));
    }
    @PutMapping("/api/tenants/{tenant}/slack-tracking")
    public SlackTrackingSettings.View save(@PathVariable String tenant,@AuthenticationPrincipal Jwt jwt,@RequestBody SlackTrackingSettings.Update update) {
        return settings.save(tenant,update,CurrentUser.from(jwt));
    }
    @GetMapping("/api/incidents/{incident}/slack-tracking")
    public List<SlackTrackingService.Tracking> list(@PathVariable long incident,@AuthenticationPrincipal Jwt jwt) {
        return tracking.list(incident,CurrentUser.from(jwt));
    }
    @PostMapping("/api/incidents/{incident}/slack-deliveries/{id}/tracking")
    @ResponseStatus(org.springframework.http.HttpStatus.NO_CONTENT)
    public void act(@PathVariable long incident,@PathVariable UUID id,@AuthenticationPrincipal Jwt jwt,@RequestBody Action action) {
        tracking.act(incident,id,action.action(),CurrentUser.from(jwt));
    }
    @PostMapping(value="/api/slack/interactions",consumes=MediaType.APPLICATION_FORM_URLENCODED_VALUE)
    public void interact(@RequestBody byte[] raw,@RequestHeader(value="X-Slack-Request-Timestamp",defaultValue="") String timestamp,
            @RequestHeader(value="X-Slack-Signature",defaultValue="") String signature) {
        tracking.interact(raw,timestamp,signature);
    }
}

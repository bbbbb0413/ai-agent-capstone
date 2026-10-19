package com.opslens.backend.settings;

import com.opslens.backend.auth.CurrentUser;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/settings")
public class SettingsController {

    public record Toggle(boolean enabled) {}

    private final SettingsService settings;

    public SettingsController(SettingsService settings) {
        this.settings = settings;
    }

    @GetMapping
    SettingsService.Settings get(@AuthenticationPrincipal Jwt jwt) {
        CurrentUser.from(jwt).requireMspStaff();
        return settings.get();
    }

    @PutMapping("/auto-approve")
    SettingsService.Settings setAutoApprove(@AuthenticationPrincipal Jwt jwt, @RequestBody Toggle toggle) {
        return settings.setAutoApprove(toggle.enabled(), CurrentUser.from(jwt));
    }
}

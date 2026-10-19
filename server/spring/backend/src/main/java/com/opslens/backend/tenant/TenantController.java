package com.opslens.backend.tenant;

import com.opslens.backend.auth.CurrentUser;
import java.util.List;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/tenants")
public class TenantController {

    private final TenantRepository tenants;

    public TenantController(TenantRepository tenants) {
        this.tenants = tenants;
    }

    /** 로그인 사용자가 볼 수 있는 고객사. 고객사 담당자는 자기 회사 하나만 나온다. */
    @GetMapping("/me")
    List<Tenant> mine(@AuthenticationPrincipal Jwt jwt) {
        return tenants.findAll(CurrentUser.from(jwt).tenants());
    }
}

package com.opslens.backend.auth;

import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.server.ResponseStatusException;

/** JWT에서 꺼낸 로그인 사용자. 권한 판단은 화면이 아니라 여기(backend)에서 한다. */
public record CurrentUser(long id, String username, String role, List<String> tenants) {

    public static final String MSP_ADMIN = "MSP_ADMIN";
    public static final String MSP_ENGINEER = "MSP_ENGINEER";
    public static final String TENANT_VIEWER = "TENANT_VIEWER";

    public static CurrentUser from(Jwt jwt) {
        Number uid = jwt.getClaim("uid");
        return new CurrentUser(uid.longValue(), jwt.getSubject(), jwt.getClaimAsString("role"),
                jwt.getClaimAsStringList("tenants"));
    }

    public boolean isAdmin() {
        return MSP_ADMIN.equals(role);
    }

    public boolean isMspStaff() {
        return MSP_ADMIN.equals(role) || MSP_ENGINEER.equals(role);
    }

    public boolean canAccess(String tenantId) {
        return tenants.contains(tenantId);
    }

    public void requireAccess(String tenantId) {
        if (!canAccess(tenantId)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "no access to tenant " + tenantId);
        }
    }

    public void requireMspStaff() {
        if (!isMspStaff()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "MSP staff only");
        }
    }
}

package com.opslens.backend.tenant;

import com.opslens.backend.audit.AuditLog;
import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.change.GitOpsRepository;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * 고객사 운영 설정 (MSP 관리자 전용, 화면에는 노출하지 않는다).
 * 설정 저장소 기준 브랜치는 E2E가 임시 브랜치로 바꿨다가 되돌리는 데 쓴다 (SPEC-fault-based-actions 5.8, 결정 4).
 */
@RestController
@RequestMapping("/api/admin/tenants")
public class TenantAdminController {

    record BranchRequest(String branch) {}

    /** main 또는 E2E 임시 브랜치만 허용한다. 아무 브랜치로나 바꾸면 검토 안 된 설정이 고객사에 반영될 수 있다 */
    private static final Pattern ALLOWED = Pattern.compile("main|e2e/[A-Za-z0-9._-]{1,64}");

    private final TenantRepository tenants;
    private final GitOpsRepository gitops;
    private final AuditLog audit;

    public TenantAdminController(TenantRepository tenants, GitOpsRepository gitops, AuditLog audit) {
        this.tenants = tenants;
        this.gitops = gitops;
        this.audit = audit;
    }

    @PutMapping("/{tenantId}/gitops-branch")
    Map<String, Object> setGitopsBranch(@AuthenticationPrincipal Jwt jwt, @PathVariable String tenantId,
            @RequestBody BranchRequest request) {
        CurrentUser user = CurrentUser.from(jwt);
        if (!user.isAdmin()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "MSP admin only");
        }
        Tenant tenant = tenants.get(tenantId);
        String branch = request.branch() == null ? "" : request.branch().strip();
        if (!tenant.gitops()) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "%s는 설정 저장소로 운영하지 않는다 (%s)".formatted(tenant.name(), tenant.changeMode()));
        }
        if (!ALLOWED.matcher(branch).matches()) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "브랜치는 main 또는 e2e/<이름>만 쓸 수 있다 (요청 '%s')".formatted(branch));
        }
        if (!gitops.branchExists(branch)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "저장소에 '%s' 브랜치가 없다".formatted(branch));
        }
        tenants.setGitopsBranch(tenantId, branch);
        audit.record(tenantId, user.username(), "GITOPS_BRANCH_CHANGED",
                Map.of("from", tenant.gitopsBranch(), "to", branch), null);
        return Map.of("tenantId", tenantId, "gitopsBranch", branch, "previous", tenant.gitopsBranch());
    }
}

package com.opslens.backend.tenant;

import java.math.BigDecimal;
import java.util.List;

/**
 * 고객사. changeMode는 조치를 반영하는 방식이다 (ADR 0001 D4).
 *   GITOPS : 원하는 상태를 바꾸는 조치는 설정 저장소(gitopsPath)에 PR로 반영한다
 *   DIRECT : 모든 조치를 고객사 Kubernetes API로 바로 반영한다
 * gitopsBranch는 설정 저장소의 기준 브랜치다. 평소 main이고, E2E 동안만 임시 브랜치다 (SPEC-fault-based-actions 5.8).
 */
public record Tenant(
        String id,
        String name,
        String slaTier,
        BigDecimal availabilityTarget,
        int responseMinutes,
        List<String> allowedActions,
        String changeMode,
        String namespace,
        String gitopsPath,
        String gitopsBranch) {

    public static final String GITOPS = "GITOPS";
    public static final String DEFAULT_BRANCH = "main";

    public Tenant(String id, String name, String slaTier, BigDecimal availabilityTarget, int responseMinutes,
            List<String> allowedActions, String changeMode, String namespace, String gitopsPath) {
        this(id, name, slaTier, availabilityTarget, responseMinutes, allowedActions, changeMode, namespace, gitopsPath,
                DEFAULT_BRANCH);
    }
    public static final String DIRECT = "DIRECT";

    public boolean gitops() {
        return GITOPS.equals(changeMode);
    }
}

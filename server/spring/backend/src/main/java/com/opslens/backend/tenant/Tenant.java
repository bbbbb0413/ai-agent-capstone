package com.opslens.backend.tenant;

import java.math.BigDecimal;
import java.util.List;

/**
 * 고객사. changeMode는 조치를 반영하는 방식이다 (ADR 0001 D4).
 *   GITOPS : 원하는 상태를 바꾸는 조치는 설정 저장소(gitopsPath)에 PR로 반영한다
 *   DIRECT : 모든 조치를 고객사 Kubernetes API로 바로 반영한다
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
        String gitopsPath) {

    public static final String GITOPS = "GITOPS";
    public static final String DIRECT = "DIRECT";

    public boolean gitops() {
        return GITOPS.equals(changeMode);
    }
}

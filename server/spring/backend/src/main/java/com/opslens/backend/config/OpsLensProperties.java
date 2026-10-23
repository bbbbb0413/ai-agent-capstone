package com.opslens.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/** application.yml의 opslens.* 설정. 비밀 값은 모두 환경변수(.env)에서 온다. */
@ConfigurationProperties("opslens")
public record OpsLensProperties(
        String jwtSecret,
        Duration jwtTtl,
        InternalTokens internalTokens,
        String alertWebhookToken,
        String agentUrl,
        String prometheusUrl,
        String dispatchMode,
        boolean autoApproveAllowed,
        Kubernetes kubernetes,
        Gitops gitops) {

    /**
     * 내부 호출별 서비스 토큰 (docs/specs/SPEC-limitations-fixes.md 3단계). 호출마다 다른 값을 쓴다.
     *   backendAgent   backend → agent (보낸다)
     *   agentBackend   agent → backend (/internal/tasks/**)
     *   mcpBackend     MCP Server → backend (/internal/tenants/**, 작업 토큰 공개키)
     *   indexerBackend postmortem-indexer·document-indexer → backend (/internal/tenants/{id}/postmortems, /internal/documents/**)
     *   gitopsWebhook  ArgoCD 알림 → backend (/internal/gitops/**)
     */
    public record InternalTokens(String backendAgent, String agentBackend, String mcpBackend, String indexerBackend,
            String gitopsWebhook) {}

    /**
     * 고객사 클러스터 접속 정보. credentialsDir에 scripts/cluster-up.sh가 만든 파일이 있다.
     *   api-server, ca.crt, {tenant}.token (고객사 네임스페이스에만 권한이 있는 ServiceAccount 토큰)
     */
    public record Kubernetes(String credentialsDir) {}

    /** 설정 저장소(GitHub). token은 이 저장소의 브랜치·PR 생성 권한만 있으면 된다. */
    /** 기준 브랜치는 고객사마다 다르다(tenants.gitops_branch). 여기에는 두지 않는다. */
    public record Gitops(String apiUrl, String repo, String token, Duration mergeTimeout) {}
}

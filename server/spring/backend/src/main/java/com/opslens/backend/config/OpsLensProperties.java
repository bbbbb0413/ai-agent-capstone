package com.opslens.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/** application.yml의 opslens.* 설정. 비밀 값은 모두 환경변수(.env)에서 온다. */
@ConfigurationProperties("opslens")
public record OpsLensProperties(
        String jwtSecret,
        Duration jwtTtl,
        String serviceToken,
        String alertWebhookToken,
        String agentUrl,
        String dispatchMode,
        Kubernetes kubernetes,
        Gitops gitops) {

    /**
     * 고객사 클러스터 접속 정보. credentialsDir에 scripts/cluster-up.sh가 만든 파일이 있다.
     *   api-server, ca.crt, {tenant}.token (고객사 네임스페이스에만 권한이 있는 ServiceAccount 토큰)
     */
    public record Kubernetes(String credentialsDir) {}

    /** 설정 저장소(GitHub). token은 이 저장소의 브랜치·PR 생성 권한만 있으면 된다. */
    public record Gitops(String apiUrl, String repo, String baseBranch, String token, Duration mergeTimeout) {}
}

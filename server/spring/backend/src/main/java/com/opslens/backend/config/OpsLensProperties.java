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
        String dispatchMode) {
}

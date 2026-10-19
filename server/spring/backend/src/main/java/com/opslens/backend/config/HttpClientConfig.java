package com.opslens.backend.config;

import java.net.http.HttpClient;
import java.time.Duration;
import org.springframework.boot.web.client.RestClientCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;

@Configuration
public class HttpClientConfig {

    /**
     * 내부 호출(agent, demo-app)은 HTTP/1.1로 고정한다.
     * JDK HttpClient 기본값은 평문 HTTP에서도 HTTP/2 업그레이드(h2c)를 시도하는데, agent(uvicorn)가 이를 거부한다.
     */
    @Bean
    RestClientCustomizer http11RestClient() {
        HttpClient client = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        return builder -> builder.requestFactory(new JdkClientHttpRequestFactory(client));
    }
}

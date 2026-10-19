package com.opslens.backend.auth;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.opslens.backend.config.OpsLensProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.security.MessageDigest;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * /internal/** 요청의 서비스 토큰을 검사한다.
 * Alertmanager webhook은 별도 토큰(ALERT_WEBHOOK_TOKEN), 나머지 내부 서비스는 SERVICE_TOKEN을 쓴다.
 */
public class ServiceTokenFilter extends OncePerRequestFilter {

    private final OpsLensProperties props;

    public ServiceTokenFilter(OpsLensProperties props) {
        this.props = props;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String expected = "/internal/alerts".equals(request.getRequestURI())
                ? props.alertWebhookToken()
                : props.serviceToken();
        String header = request.getHeader("Authorization");
        String given = header != null && header.startsWith("Bearer ") ? header.substring(7).trim() : null;

        if (expected == null || expected.isBlank() || given == null
                || !MessageDigest.isEqual(expected.getBytes(UTF_8), given.getBytes(UTF_8))) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "invalid service token");
            return;
        }
        chain.doFilter(request, response);
    }
}

package com.opslens.backend.auth;

import static java.nio.charset.StandardCharsets.UTF_8;

import com.opslens.backend.config.OpsLensProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.security.MessageDigest;
import java.util.List;
import java.util.function.Function;
import org.springframework.util.AntPathMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * /internal/** 요청의 서비스 토큰을 경로별로 검사한다. 경로마다 받는 토큰이 하나다 (SPEC-limitations-fixes 3단계).
 * 표에 없는 경로는 막는다. 통과한 요청에는 호출한 쪽 이름(CALLER)을 남긴다.
 */
public class ServiceTokenFilter extends OncePerRequestFilter {

    /** 요청 속성: 토큰으로 확인한 호출자 (alertmanager, argocd, agent, mcp, indexer) */
    public static final String CALLER = ServiceTokenFilter.class.getName() + ".caller";

    record Route(String pattern, String caller, Function<OpsLensProperties, String> token) {}

    /** 위에서부터 처음 맞는 경로의 토큰을 쓴다. 회고 경로는 고객사 경로보다 먼저 둔다. 문서 색인은 document-indexer다 */
    static final List<Route> ROUTES = List.of(
            new Route("/internal/alerts", "alertmanager", OpsLensProperties::alertWebhookToken),
            new Route("/internal/gitops/**", "argocd", p -> p.internalTokens().gitopsWebhook()),
            new Route("/internal/tasks/**", "agent", p -> p.internalTokens().agentBackend()),
            new Route("/internal/.well-known/jwks.json", "mcp", p -> p.internalTokens().mcpBackend()),
            new Route("/internal/documents/**", "indexer", p -> p.internalTokens().indexerBackend()),
            new Route("/internal/tenants/*/postmortems", "indexer", p -> p.internalTokens().indexerBackend()),
            new Route("/internal/tenants/**", "mcp", p -> p.internalTokens().mcpBackend()));

    private static final AntPathMatcher PATHS = new AntPathMatcher();

    private final OpsLensProperties props;

    public ServiceTokenFilter(OpsLensProperties props) {
        this.props = props;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        Route route = ROUTES.stream().filter(r -> PATHS.match(r.pattern(), path)).findFirst().orElse(null);
        String expected = route == null ? null : route.token().apply(props);
        String header = request.getHeader("Authorization");
        String given = header != null && header.startsWith("Bearer ") ? header.substring(7).trim() : null;

        if (expected == null || expected.isBlank() || given == null
                || !MessageDigest.isEqual(expected.getBytes(UTF_8), given.getBytes(UTF_8))) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "invalid service token");
            return;
        }
        request.setAttribute(CALLER, route.caller());
        chain.doFilter(request, response);
    }
}

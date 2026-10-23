package com.opslens.backend.auth;

import static org.junit.jupiter.api.Assertions.*;

import com.opslens.backend.config.OpsLensProperties;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** 내부 호출 토큰: 기동 검사와 경로별 토큰 (SPEC-limitations-fixes 3단계) */
class InternalTokensTest {

    private static OpsLensProperties props(String backendAgent, String agentBackend, String mcpBackend,
            String indexerBackend, String gitopsWebhook, String alertWebhook) {
        return new OpsLensProperties("jwt", null, new OpsLensProperties.InternalTokens(backendAgent, agentBackend,
                mcpBackend, indexerBackend, gitopsWebhook), alertWebhook, null, null, null, false, null, null);
    }

    private static final OpsLensProperties OK = props("t-ba", "t-ab", "t-mb", "t-ib", "t-gw", "t-alert");

    @Test
    void startupNeedsEveryTokenAndDistinctValues() {
        InternalTokenCheck.check(OK);
        var missing = assertThrows(IllegalStateException.class,
                () -> InternalTokenCheck.check(props("t-ba", "", "t-mb", "t-ib", "t-gw", "t-alert")));
        assertTrue(missing.getMessage().contains("AGENT_BACKEND_TOKEN"));
        var shared = assertThrows(IllegalStateException.class,
                () -> InternalTokenCheck.check(props("same", "t-ab", "t-mb", "same", "t-gw", "t-alert")));
        assertTrue(shared.getMessage().contains("BACKEND_AGENT_TOKEN") && shared.getMessage().contains("INDEXER_BACKEND_TOKEN"));
        assertFalse(shared.getMessage().contains("same"));   // 값은 메시지에 남기지 않는다
    }

    private static int call(String path, String token) throws Exception {
        var request = new MockHttpServletRequest("POST", path);
        request.setRequestURI(path);
        if (token != null) {
            request.addHeader("Authorization", "Bearer " + token);
        }
        var response = new MockHttpServletResponse();
        FilterChain chain = (req, res) -> ((MockHttpServletResponse) res).setStatus(200);
        new ServiceTokenFilter(OK).doFilter(request, response, chain);
        return response.getStatus();
    }

    @Test
    void eachPathAcceptsOnlyItsOwnToken() throws Exception {
        String[][] cases = {
                {"/internal/tasks/1/events", "t-ab"},
                {"/internal/tasks/1/token", "t-ab"},
                {"/internal/tenants/tenant-a/services", "t-mb"},
                {"/internal/.well-known/jwks.json", "t-mb"},
                {"/internal/tenants/tenant-a/postmortems", "t-ib"},
                {"/internal/documents/versions/1", "t-ib"},
                {"/internal/documents/pending", "t-ib"},
                {"/internal/tenants/tenant-a/documents/runbook-order-api", "t-mb"},
                {"/internal/gitops/sync-events", "t-gw"},
                {"/internal/alerts", "t-alert"},
        };
        String[] all = {"t-ab", "t-mb", "t-ib", "t-gw", "t-alert", "t-ba"};
        for (String[] c : cases) {
            for (String token : all) {
                int expected = token.equals(c[1]) ? 200 : 401;
                assertEquals(expected, call(c[0], token), c[0] + " with " + token);
            }
        }
        assertEquals(401, call("/internal/tasks/1/events", null));
        assertEquals(401, call("/internal/unknown", "t-ab"));   // 표에 없는 경로는 막는다
    }
}

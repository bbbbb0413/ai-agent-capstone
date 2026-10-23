package com.opslens.backend.change;

import static org.hamcrest.Matchers.allOf;
import static org.hamcrest.Matchers.containsString;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.tenant.Tenant;
import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

/** 설정 저장소의 변경 이력은 서비스 매니페스트 단위다 (SPEC-e2e-speed 8.2). 같은 고객사의 다른 서비스 커밋이 섞이지 않는다. */
class GitOpsRepositoryTest {

    private final Tenant tenantA = new Tenant("tenant-a", "A사", "GOLD", new BigDecimal("99.90"), 15,
            List.of("rollback_deployment"), "GITOPS", "tenant-a", "gitops/tenants/tenant-a");

    @Test
    void manifestIsTheServiceFileUnderTheTenantPath() {
        assertEquals("gitops/tenants/tenant-a/cart-api.yaml", GitOpsRepository.manifest(tenantA, "cart-api"));
    }

    @Test
    void recentCommitsAreThoseOfTheServiceManifestAndShowOnlyItsPatch() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        GitOpsRepository gitops = new GitOpsRepository(builder,
                new OpsLensProperties("jwt", null, null, null, null, null, null, false, null,
                        new OpsLensProperties.Gitops("https://api.github.test", "o/r", "", null)));
        server.expect(requestTo(allOf(containsString("/repos/o/r/commits?sha=main"),
                        // URI 변수라 /가 %2F로 인코딩된다. GitHub는 그대로 받는다 (고객사 경로로 볼 때도 같았다)
                        containsString("path=gitops%2Ftenants%2Ftenant-a%2Fcart-api.yaml"), containsString("per_page=3"))))
                .andRespond(withSuccess("""
                        [{"sha": "abcdef1234567", "commit": {"message": "Tune cart-api DB connection pool to 5",
                          "author": {"name": "e2e", "date": "2026-10-23T05:00:00Z"}}}]
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo(containsString("/repos/o/r/commits/abcdef1234567")))
                .andRespond(withSuccess("""
                        {"files": [{"filename": "gitops/tenants/tenant-a/cart-api.yaml", "patch": "-value: \\"20\\"\\n+value: \\"5\\""},
                                   {"filename": "gitops/tenants/tenant-a/order-api.yaml", "patch": "other service"}]}
                        """, MediaType.APPLICATION_JSON));

        List<GitOpsRepository.Commit> commits = gitops.recentCommits(tenantA, "cart-api", 3);

        server.verify();
        assertEquals(1, commits.size());
        assertEquals("abcdef1", commits.get(0).sha());
        assertTrue(commits.get(0).patch().contains("cart-api.yaml"));
        assertFalse(commits.get(0).patch().contains("other service"));   // 같은 커밋의 다른 서비스 파일은 빼고 보여 준다
    }
}

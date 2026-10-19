package com.opslens.backend.change;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.tenant.Tenant;
import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

/**
 * 고객사 Kubernetes API를 고객사 ServiceAccount로 호출한다. CLI(kubectl)는 쓰지 않는다 (ADR 0001 D2).
 * 토큰은 고객사 네임스페이스에만 권한이 있어서, 다른 고객사 네임스페이스 호출은 API 서버가 거부한다.
 * 서비스 이름이 곧 Deployment 이름이고, 파드는 app=<서비스> 라벨을 단다 (gitops/tenants/*).
 */
@Component
public class KubernetesClient {

    private static final MediaType MERGE_PATCH = MediaType.parseMediaType("application/merge-patch+json");
    private static final MediaType JSON_PATCH = MediaType.parseMediaType("application/json-patch+json");
    private static final String REVISION = "deployment.kubernetes.io/revision";

    private final RestClient.Builder builder;
    private final Path credentials;

    public KubernetesClient(RestClient.Builder builder, OpsLensProperties props) {
        this.builder = builder;
        this.credentials = Path.of(props.kubernetes().credentialsDir());
    }

    public record Deployment(String name, int replicas, int readyReplicas, String image, Map<String, String> env) {}

    public record Revision(int revision, String image, Map<String, String> env, String createdAt) {}

    public Deployment deployment(Tenant tenant, String service) {
        return toDeployment(call(tenant, c -> c.get().uri(deploymentPath(tenant, service))
                .retrieve().body(JsonNode.class)));
    }

    /** 파드를 지운다. Deployment가 새 파드를 만든다. ArgoCD가 관리하는 원하는 상태는 바뀌지 않는다. */
    public List<String> restartPods(Tenant tenant, String service) {
        JsonNode pods = call(tenant, c -> c.get()
                .uri("/api/v1/namespaces/{ns}/pods?labelSelector={selector}", tenant.namespace(), "app=" + service)
                .retrieve().body(JsonNode.class));
        List<String> deleted = new ArrayList<>();
        for (JsonNode pod : pods.path("items")) {
            String name = pod.path("metadata").path("name").asText();
            call(tenant, c -> c.delete().uri("/api/v1/namespaces/{ns}/pods/{name}", tenant.namespace(), name)
                    .retrieve().toBodilessEntity());
            deleted.add(name);
        }
        return deleted;
    }

    public void scale(Tenant tenant, String service, int replicas) {
        call(tenant, c -> c.patch().uri(deploymentPath(tenant, service) + "/scale").contentType(MERGE_PATCH)
                .body(Map.of("spec", Map.of("replicas", replicas))).retrieve().toBodilessEntity());
    }

    /** 직전 ReplicaSet의 파드 템플릿으로 되돌린다 (kubectl rollout undo와 같은 동작). */
    public Revision rollback(Tenant tenant, String service) {
        List<JsonNode> sets = replicaSets(tenant, service);
        if (sets.size() < 2) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "%s에 되돌릴 이전 배포 이력이 없다".formatted(service));
        }
        JsonNode previous = sets.get(1);
        ObjectNode template = previous.path("spec").path("template").deepCopy();
        ((ObjectNode) template.path("metadata").path("labels")).remove("pod-template-hash");
        call(tenant, c -> c.patch().uri(deploymentPath(tenant, service)).contentType(JSON_PATCH)
                .body(List.of(Map.of("op", "replace", "path", "/spec/template", "value", template)))
                .retrieve().toBodilessEntity());
        return toRevision(previous);
    }

    /** 최근 배포 이력 (최신순). DIRECT 고객사의 list_recent_changes에 쓴다. */
    public List<Revision> history(Tenant tenant, String service) {
        return replicaSets(tenant, service).stream().map(KubernetesClient::toRevision).toList();
    }

    private List<JsonNode> replicaSets(Tenant tenant, String service) {
        JsonNode sets = call(tenant, c -> c.get()
                .uri("/apis/apps/v1/namespaces/{ns}/replicasets?labelSelector={selector}", tenant.namespace(), "app=" + service)
                .retrieve().body(JsonNode.class));
        List<JsonNode> items = new ArrayList<>();
        sets.path("items").forEach(items::add);
        items.sort(Comparator.comparingInt(KubernetesClient::revisionOf).reversed());
        return items;
    }

    private static String deploymentPath(Tenant tenant, String service) {
        return "/apis/apps/v1/namespaces/" + tenant.namespace() + "/deployments/" + service;
    }

    // ── 응답 변환 ──────────────────────────────────────────────

    private static Deployment toDeployment(JsonNode d) {
        JsonNode container = d.path("spec").path("template").path("spec").path("containers").path(0);
        return new Deployment(d.path("metadata").path("name").asText(), d.path("spec").path("replicas").asInt(),
                d.path("status").path("readyReplicas").asInt(), container.path("image").asText(), env(container));
    }

    private static Revision toRevision(JsonNode rs) {
        JsonNode container = rs.path("spec").path("template").path("spec").path("containers").path(0);
        return new Revision(revisionOf(rs), container.path("image").asText(), env(container),
                rs.path("metadata").path("creationTimestamp").asText());
    }

    private static int revisionOf(JsonNode rs) {
        return rs.path("metadata").path("annotations").path(REVISION).asInt();
    }

    /** 값이 직접 적힌 환경변수만 돌려준다. Secret 참조(valueFrom)는 내보내지 않는다. */
    private static Map<String, String> env(JsonNode container) {
        Map<String, String> env = new java.util.TreeMap<>();
        for (JsonNode e : container.path("env")) {
            if (e.has("value")) {
                env.put(e.path("name").asText(), e.path("value").asText());
            }
        }
        return env;
    }

    // ── 접속 ───────────────────────────────────────────────────

    private interface Call<T> {
        T run(RestClient client);
    }

    private <T> T call(Tenant tenant, Call<T> call) {
        try {
            return call.run(client(tenant));
        } catch (RestClientResponseException e) {
            HttpStatus status = e.getStatusCode().value() == 404 ? HttpStatus.NOT_FOUND : HttpStatus.BAD_GATEWAY;
            throw new ResponseStatusException(status, "Kubernetes API %d: %s"
                    .formatted(e.getStatusCode().value(), e.getResponseBodyAsString().lines().findFirst().orElse("")));
        }
    }

    /** 클러스터가 backend보다 늦게 뜰 수 있어서 호출할 때 접속 정보를 읽는다. */
    private RestClient client(Tenant tenant) {
        try {
            String server = Files.readString(credentials.resolve("api-server")).strip();
            String token = Files.readString(credentials.resolve(tenant.id() + ".token")).strip();
            HttpClient http = HttpClient.newBuilder()
                    .version(HttpClient.Version.HTTP_1_1)
                    .connectTimeout(Duration.ofSeconds(5))
                    .sslContext(sslContext(credentials.resolve("ca.crt")))
                    .build();
            return builder.clone().baseUrl(server).requestFactory(new JdkClientHttpRequestFactory(http))
                    .defaultHeader("Authorization", "Bearer " + token).build();
        } catch (IOException | java.security.GeneralSecurityException e) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "고객사 클러스터 접속 정보를 읽지 못했다(%s). scripts/cluster-up.sh를 실행했는지 확인한다"
                            .formatted(e.getClass().getSimpleName()));
        }
    }

    private static SSLContext sslContext(Path caFile) throws IOException, java.security.GeneralSecurityException {
        KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
        trust.load(null, null);
        try (InputStream in = Files.newInputStream(caFile)) {
            trust.setCertificateEntry("tenant-cluster", CertificateFactory.getInstance("X.509").generateCertificate(in));
        }
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init(trust);
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(null, factory.getTrustManagers(), null);
        return context;
    }
}

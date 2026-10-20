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
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
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
 * Kubernetes 실행 환경 어댑터. 고객사 Kubernetes API를 고객사 ServiceAccount로 호출한다. CLI(kubectl)는 쓰지 않는다 (ADR 0001 D2).
 * 토큰은 고객사 네임스페이스에만 권한이 있어서, 다른 고객사 네임스페이스 호출은 API 서버가 거부한다.
 * 네임스페이스는 서비스 카탈로그의 target_namespace다. 서비스 이름이 곧 Deployment 이름이고, 파드는 app=<서비스> 라벨을 단다.
 */
@Component
public class KubernetesClient implements RuntimeAdapter {

    private static final MediaType MERGE_PATCH = MediaType.parseMediaType("application/merge-patch+json");
    private static final MediaType JSON_PATCH = MediaType.parseMediaType("application/json-patch+json");
    private static final String REVISION = "deployment.kubernetes.io/revision";
    /** 순차 재시작에서 새 파드 하나가 Ready가 되기를 기다리는 최대 시간 */
    private static final Duration POD_READY_TIMEOUT = Duration.ofSeconds(60);

    private final RestClient.Builder builder;
    private final Path credentials;

    public KubernetesClient(RestClient.Builder builder, OpsLensProperties props) {
        this.builder = builder;
        this.credentials = Path.of(props.kubernetes().credentialsDir());
    }

    @Override
    public String runtime() {
        return "kubernetes";
    }

    @Override
    public Set<String> supportedActions() {
        return Set.of("restart_pods", "scale_deployment", "rollback_deployment", "adjust_autoscaler");
    }

    @Override
    public Workload status(Tenant tenant, ServiceCatalog.Entry service) {
        JsonNode d = call(tenant, c -> c.get().uri(deploymentPath(service)).retrieve().body(JsonNode.class));
        JsonNode container = d.path("spec").path("template").path("spec").path("containers").path(0);
        return new Workload(d.path("metadata").path("name").asText(), d.path("spec").path("replicas").asInt(),
                d.path("status").path("readyReplicas").asInt(), container.path("image").asText(), env(container),
                autoscaler(tenant, service));
    }

    /** 이 Deployment를 대상으로 하는 HPA. 없으면 null. */
    private Autoscaler autoscaler(Tenant tenant, ServiceCatalog.Entry service) {
        JsonNode list = call(tenant, c -> c.get().uri("/apis/autoscaling/v2/namespaces/{ns}/horizontalpodautoscalers",
                service.targetNamespace()).retrieve().body(JsonNode.class));
        for (JsonNode hpa : list.path("items")) {
            JsonNode target = hpa.path("spec").path("scaleTargetRef");
            if ("Deployment".equals(target.path("kind").asText()) && service.name().equals(target.path("name").asText())) {
                return new Autoscaler(hpa.path("metadata").path("name").asText(),
                        hpa.path("spec").path("minReplicas").asInt(1), hpa.path("spec").path("maxReplicas").asInt(),
                        hpa.path("status").path("currentReplicas").asInt(),
                        hpa.path("status").path("desiredReplicas").asInt(),
                        cpu(hpa.path("spec").path("metrics"), "target"),
                        cpu(hpa.path("status").path("currentMetrics"), "current"));
            }
        }
        return null;
    }

    private static Integer cpu(JsonNode metrics, String field) {
        for (JsonNode m : metrics) {
            JsonNode resource = m.path("resource");
            if ("cpu".equals(resource.path("name").asText()) && resource.path(field).has("averageUtilization")) {
                return resource.path(field).path("averageUtilization").asInt();
            }
        }
        return null;
    }

    @Override
    public void setAutoscalerMax(Tenant tenant, ServiceCatalog.Entry service, Autoscaler autoscaler, int maxReplicas) {
        call(tenant, c -> c.patch().uri("/apis/autoscaling/v2/namespaces/{ns}/horizontalpodautoscalers/{name}",
                        service.targetNamespace(), autoscaler.name()).contentType(MERGE_PATCH)
                .body(Map.of("spec", Map.of("maxReplicas", maxReplicas))).retrieve().toBodilessEntity());
    }

    /**
     * 파드를 지워 재시작한다. Deployment가 새 파드를 만들고, ArgoCD가 관리하는 원하는 상태는 바뀌지 않는다.
     * 레플리카가 2개 이상이면 하나씩 지우고, 지운 파드가 사라지고 Ready 파드 수가 원래대로 돌아온 뒤 다음 파드를 지운다.
     * 한꺼번에 지우면 레플리카가 여럿이어도 잠깐 전체가 멈춘다 (SPEC-fault-based-actions 1.3).
     */
    @Override
    public Restart restart(Tenant tenant, ServiceCatalog.Entry service) {
        List<String> pods = podNames(tenant, service);
        boolean rolling = pods.size() >= 2;
        List<String> restarted = new ArrayList<>();
        for (String name : pods) {
            call(tenant, c -> c.delete().uri("/api/v1/namespaces/{ns}/pods/{name}", service.targetNamespace(), name)
                    .retrieve().toBodilessEntity());
            restarted.add(name);
            if (rolling) {
                awaitReplaced(tenant, service, name, pods.size(), restarted);
            }
        }
        return new Restart(restarted, rolling);
    }

    @Override
    public void scale(Tenant tenant, ServiceCatalog.Entry service, int replicas) {
        call(tenant, c -> c.patch().uri(deploymentPath(service) + "/scale").contentType(MERGE_PATCH)
                .body(Map.of("spec", Map.of("replicas", replicas))).retrieve().toBodilessEntity());
    }

    /**
     * 장애 시작 전 마지막 배포의 바로 이전 ReplicaSet 파드 템플릿으로 되돌린다 (kubectl rollout undo와 비슷하다).
     * 기준을 장애 시작 시각으로 잡아서, 롤백을 두 번 해도 원래 장애 배포로 돌아가지 않는다.
     */
    @Override
    public Revision rollback(Tenant tenant, ServiceCatalog.Entry service, OffsetDateTime incidentStartedAt) {
        List<JsonNode> sets = replicaSets(tenant, service);
        List<JsonNode> byCreation = new ArrayList<>(sets);
        byCreation.sort(Comparator.comparing((JsonNode rs) -> createdAt(rs)).reversed());
        int culprit = -1;
        for (int i = 0; i < byCreation.size(); i++) {
            if (!createdAt(byCreation.get(i)).isAfter(incidentStartedAt)) {
                culprit = i;
                break;
            }
        }
        if (culprit < 0 || culprit + 1 >= byCreation.size()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "%s에 장애 시작 전 배포보다 이전 이력이 없다".formatted(service.name()));
        }
        JsonNode previous = byCreation.get(culprit + 1);
        if (sets.get(0).path("metadata").path("name").equals(previous.path("metadata").path("name"))) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "%s는 이미 장애 시작 전 배포(revision %d)로 되돌려져 있다"
                    .formatted(service.name(), revisionOf(previous)));
        }
        ObjectNode template = previous.path("spec").path("template").deepCopy();
        ((ObjectNode) template.path("metadata").path("labels")).remove("pod-template-hash");
        call(tenant, c -> c.patch().uri(deploymentPath(service)).contentType(JSON_PATCH)
                .body(List.of(Map.of("op", "replace", "path", "/spec/template", "value", template)))
                .retrieve().toBodilessEntity());
        return toRevision(previous, null);
    }

    /** 최근 배포 이력 (최신순). DIRECT 고객사의 list_recent_changes에 쓴다. */
    @Override
    public List<Revision> history(Tenant tenant, ServiceCatalog.Entry service) {
        List<JsonNode> sets = replicaSets(tenant, service);
        if (sets.isEmpty()) {
            return List.of();
        }
        // 현재 리비전이 언제 배포됐는지는 Deployment의 Progressing 상태가 마지막으로 바뀐 시각으로 본다.
        // ReplicaSet 생성 시각만 보면 예전 설정으로 다시 배포했을 때 "직전 변경 없음"이 된다 (b-pool E2E)
        JsonNode deployment = call(tenant, c -> c.get().uri(deploymentPath(service)).retrieve().body(JsonNode.class));
        String deployedAt = null;
        for (JsonNode condition : deployment.path("status").path("conditions")) {
            if ("Progressing".equals(condition.path("type").asText())) {
                deployedAt = condition.path("lastUpdateTime").asText(null);
            }
        }
        List<Revision> revisions = new ArrayList<>();
        for (int i = 0; i < sets.size(); i++) {
            revisions.add(toRevision(sets.get(i), i == 0 ? deployedAt : null));
        }
        return revisions;
    }

    // ── 순차 재시작 ────────────────────────────────────────────

    private List<String> podNames(Tenant tenant, ServiceCatalog.Entry service) {
        List<String> names = new ArrayList<>();
        pods(tenant, service).path("items").forEach(p -> names.add(p.path("metadata").path("name").asText()));
        return names;
    }

    private JsonNode pods(Tenant tenant, ServiceCatalog.Entry service) {
        return call(tenant, c -> c.get().uri("/api/v1/namespaces/{ns}/pods?labelSelector={selector}",
                service.targetNamespace(), "app=" + service.name()).retrieve().body(JsonNode.class));
    }

    /** 종료 중이 아닌 Ready 파드가 원래 수만큼 될 때까지 기다린다. 지운 파드는 종료 중이라 세지 않는다. */
    private void awaitReplaced(Tenant tenant, ServiceCatalog.Entry service, String deleted, int expected,
            List<String> restarted) {
        long deadline = System.nanoTime() + POD_READY_TIMEOUT.toNanos();
        while (System.nanoTime() < deadline) {
            int ready = 0;
            for (JsonNode pod : pods(tenant, service).path("items")) {
                boolean terminating = !pod.path("metadata").path("deletionTimestamp").isMissingNode()
                        || deleted.equals(pod.path("metadata").path("name").asText());
                for (JsonNode condition : pod.path("status").path("conditions")) {
                    if (!terminating && "Ready".equals(condition.path("type").asText())
                            && "True".equals(condition.path("status").asText())) {
                        ready++;
                    }
                }
            }
            if (ready >= expected) {
                return;
            }
            try {
                Thread.sleep(2_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        throw new ResponseStatusException(HttpStatus.GATEWAY_TIMEOUT,
                "순차 재시작 중 새 파드가 %d초 안에 Ready가 되지 않아 멈췄다. 재시작한 파드: %s. 남은 파드는 재시작하지 않았다"
                        .formatted(POD_READY_TIMEOUT.toSeconds(), restarted));
    }

    private List<JsonNode> replicaSets(Tenant tenant, ServiceCatalog.Entry service) {
        JsonNode sets = call(tenant, c -> c.get().uri("/apis/apps/v1/namespaces/{ns}/replicasets?labelSelector={selector}",
                service.targetNamespace(), "app=" + service.name()).retrieve().body(JsonNode.class));
        List<JsonNode> items = new ArrayList<>();
        sets.path("items").forEach(items::add);
        items.sort(Comparator.comparingInt(KubernetesClient::revisionOf).reversed());
        return items;
    }

    private static String deploymentPath(ServiceCatalog.Entry service) {
        return "/apis/apps/v1/namespaces/" + service.targetNamespace() + "/deployments/" + service.name();
    }

    // ── 응답 변환 ──────────────────────────────────────────────

    private static Revision toRevision(JsonNode rs, String deployedAt) {
        JsonNode container = rs.path("spec").path("template").path("spec").path("containers").path(0);
        return new Revision(revisionOf(rs), container.path("image").asText(), env(container),
                rs.path("metadata").path("creationTimestamp").asText(), deployedAt);
    }

    private static OffsetDateTime createdAt(JsonNode rs) {
        return OffsetDateTime.parse(rs.path("metadata").path("creationTimestamp").asText());
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

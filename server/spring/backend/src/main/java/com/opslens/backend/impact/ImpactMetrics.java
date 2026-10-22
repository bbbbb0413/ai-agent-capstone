package com.opslens.backend.impact;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.config.OpsLensProperties;
import java.net.http.HttpClient;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** 고객사별 일괄 조회. 서비스 수가 늘어도 Prometheus 요청은 다섯 번이다. */
@Component
public class ImpactMetrics {
    public record Key(String service, String dependency) {}
    public record Snapshot(Map<Key, Double> requests, Map<Key, Double> errors, Map<Key, Double> latency,
            Map<Key, Double> dependencyCalls, Map<Key, Double> dependencyLatency, List<String> warnings) {}

    private final RestClient client;

    public ImpactMetrics(RestClient.Builder builder, OpsLensProperties props) {
        var factory = new JdkClientHttpRequestFactory(HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2)).version(HttpClient.Version.HTTP_1_1).build());
        factory.setReadTimeout(Duration.ofSeconds(2));
        client = builder.clone().baseUrl(props.prometheusUrl()).requestFactory(factory).build();
    }

    public Snapshot read(String tenant, Instant at) {
        String label = "tenant=\"" + escape(tenant) + "\"";
        var warnings = new ArrayList<String>();
        var requests = query("sum by (tenant,service) (rate(demo_orders_seconds_count{" + label + "}[1m]))", tenant, at, warnings);
        // 없는 error 시계열은 동일 서비스의 요청 시계열로 0을 채운다. 조회 실패는 0이 아니다.
        var errors = query("sum by (tenant,service) (rate(demo_orders_seconds_count{" + label
                + ",outcome=\"error\"}[1m])) or on (tenant,service) (0 * sum by (tenant,service) (rate(demo_orders_seconds_count{"
                + label + "}[1m])))", tenant, at, warnings);
        var latency = query("histogram_quantile(0.95, sum by (tenant,service,le) (rate(demo_orders_seconds_bucket{"
                + label + "}[1m])))", tenant, at, warnings);
        var calls = query("sum by (tenant,service,dependency) (rate(demo_dependency_seconds_count{" + label + "}[1m]))", tenant, at, warnings);
        var dependencyLatency = query("histogram_quantile(0.95, sum by (tenant,service,dependency,le) (rate(demo_dependency_seconds_bucket{"
                + label + "}[1m])))", tenant, at, warnings);
        return new Snapshot(requests, errors, latency, calls, dependencyLatency, List.copyOf(warnings));
    }

    private Map<Key, Double> query(String expression, String tenant, Instant at, List<String> warnings) {
        try {
            JsonNode body = client.get().uri(u -> u.path("/api/v1/query")
                    .queryParam("query", "{query}").queryParam("time", "{time}")
                    .build(expression, at.toString())).retrieve().body(JsonNode.class);
            if (body == null || !"success".equals(body.path("status").asText())
                    || !"vector".equals(body.path("data").path("resultType").asText()))
                throw new IllegalStateException("invalid metrics response");
            var result = new HashMap<Key, Double>();
            for (JsonNode row : body.path("data").path("result")) {
                JsonNode labels = row.path("metric");
                if (!tenant.equals(labels.path("tenant").asText())) continue;
                String service = labels.path("service").asText();
                if (service.isBlank()) continue;
                double value = Double.parseDouble(row.path("value").path(1).asText());
                if (Double.isFinite(value) && value >= 0)
                    result.put(new Key(service, labels.path("dependency").asText("")), value);
            }
            if (body.hasNonNull("warnings")) warnings.add("지표 서버가 일부 데이터에 대한 경고를 반환했습니다.");
            return result;
        } catch (RuntimeException e) {
            warnings.add("일부 지표를 조회하지 못했습니다. 해당 항목은 확인 불가로 표시합니다.");
            return Map.of();
        }
    }

    static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }
}

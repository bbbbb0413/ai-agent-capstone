package com.opslens.backend.change;

import com.fasterxml.jackson.databind.JsonNode;
import com.opslens.backend.config.OpsLensProperties;
import com.opslens.backend.tenant.Tenant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

/**
 * 의존 대상(DB, 외부 API) 상태 (SPEC-fault-based-actions 5.1·5.6). 앱이 의존 호출마다 남기는 demo_dependency_seconds를
 * 최근 1분 p95로 보고 정상·지연·장애를 정한다. 트래픽 급증(앱 병목), 외부 지연, DB 자체 지연을 구분하는 근거다.
 * 판정 기준은 코드가 정한다. 모델은 이 값을 근거로 쓰기만 한다.
 */
@Component
public class DependencyHealth {

    static final double DEGRADED_SECONDS = 0.3;
    static final double DOWN_SECONDS = 3.0;

    private final ServiceCatalog catalog;
    private final RestClient prometheus;

    public DependencyHealth(ServiceCatalog catalog, RestClient.Builder builder, OpsLensProperties props) {
        this.catalog = catalog;
        this.prometheus = builder.clone().baseUrl(props.prometheusUrl()).build();
    }

    public Map<String, Object> health(Tenant tenant, String name) {
        List<ServiceCatalog.Entry> entries = catalog.list(tenant.id());
        ServiceCatalog.Entry entry = entries.stream()
                .filter(e -> e.dependencies().stream().anyMatch(d -> d.name().equals(name))).findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                        "'%s'는 %s의 의존 대상이 아니다. 의존 대상: %s".formatted(name, tenant.name(), entries.stream()
                                .flatMap(e -> e.dependencies().stream()).map(ServiceCatalog.Dependency::name).toList())));
        ServiceCatalog.Dependency dep = entry.dependencies().stream().filter(d -> d.name().equals(name)).findFirst()
                .orElseThrow();
        String selector = "tenant=\"%s\",service=\"%s\",dependency=\"%s\"".formatted(tenant.id(), entry.name(), name);
        Double p95 = query("histogram_quantile(0.95, sum by (le) (rate(demo_dependency_seconds_bucket{%s}[1m])))"
                .formatted(selector));
        Double rate = query("sum(rate(demo_dependency_seconds_count{%s}[1m]))".formatted(selector));

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("dependency", name);
        result.put("service", entry.name());
        result.put("kind", dep.kind());
        result.put("owner", dep.owner());
        result.put("ownerContact", dep.ownerContact());
        result.put("status", status(p95, rate));
        result.put("p95Seconds", p95);
        result.put("callsPerSecond", rate);
        result.put("window", "최근 1분");
        result.put("thresholds", Map.of("degradedSeconds", DEGRADED_SECONDS, "downSeconds", DOWN_SECONDS));
        return result;
    }

    /** 호출이 없으면 판단하지 않는다(UNKNOWN). 지연은 호출한 앱 쪽에서 잰 값이다. */
    static String status(Double p95, Double rate) {
        if (p95 == null || rate == null || rate <= 0) {
            return "UNKNOWN";
        }
        return p95 >= DOWN_SECONDS ? "DOWN" : p95 >= DEGRADED_SECONDS ? "DEGRADED" : "HEALTHY";
    }

    private Double query(String expr) {
        try {
            JsonNode body = prometheus.get().uri(u -> u.path("/api/v1/query").queryParam("query", "{q}").build(expr))
                    .retrieve().body(JsonNode.class);
            JsonNode value = body.path("data").path("result").path(0).path("value").path(1);
            if (value.isMissingNode()) {
                return null;
            }
            double v = Double.parseDouble(value.asText());
            return Double.isNaN(v) ? null : Math.round(v * 1000) / 1000.0;
        } catch (RestClientException | NumberFormatException e) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "메트릭 조회에 실패했다: " + e.getMessage());
        }
    }
}

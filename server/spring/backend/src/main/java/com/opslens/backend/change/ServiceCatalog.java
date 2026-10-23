package com.opslens.backend.change;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opslens.backend.tenant.Tenant;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

/**
 * 서비스 카탈로그 (SPEC-fault-based-actions 4.1). 고객사 서비스의 실행 환경, 의존 대상, 용량 제약.
 * 실제 운영의 CMDB·서비스 카탈로그 역할이며, 조치의 구조 검토(②층)와 반영 방식(④층)에 쓴다.
 */
@Repository
public class ServiceCatalog {

    public record Dependency(String name, String kind, String owner, String ownerContact, Integer maxConnections,
            BigDecimal connectionHeadroom) {

        public boolean database() {
            return "database".equals(kind);
        }
    }

    /** 진단·회복 확인 쿼리를 만드는 관측 정의 (SPEC-limitations-fixes 4단계). 서비스 조건은 agent가 붙인다. */
    public record Observability(String requestMetric, String errorSelector, String logSelector,
            Map<String, String> logSignals) {}

    public record Entry(String tenantId, String name, String runtime, String targetCluster, String targetNamespace,
            boolean stateful, int minReplicas, int maxReplicas, Integer poolSize, List<Dependency> dependencies,
            String poolSizeEnv, Observability observability) {}

    private static final TypeReference<Map<String, String>> SIGNALS = new TypeReference<>() {};

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public ServiceCatalog(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public List<Entry> list(String tenantId) {
        Map<String, List<Dependency>> deps = jdbc.sql("""
                        SELECT service, name, kind, owner, owner_contact, max_connections, connection_headroom
                        FROM service_dependencies WHERE tenant_id = ? ORDER BY service, name
                        """)
                .param(tenantId)
                .query((rs, n) -> Map.entry(rs.getString("service"), new Dependency(rs.getString("name"),
                        rs.getString("kind"), rs.getString("owner"), rs.getString("owner_contact"),
                        rs.getObject("max_connections", Integer.class), rs.getBigDecimal("connection_headroom"))))
                .list().stream()
                .collect(Collectors.groupingBy(Map.Entry::getKey,
                        Collectors.mapping(Map.Entry::getValue, Collectors.toCollection(ArrayList::new))));
        return jdbc.sql("SELECT * FROM services WHERE tenant_id = ? ORDER BY name")
                .param(tenantId)
                .query((rs, n) -> new Entry(rs.getString("tenant_id"), rs.getString("name"), rs.getString("runtime"),
                        rs.getString("target_cluster"), rs.getString("target_namespace"), rs.getBoolean("stateful"),
                        rs.getInt("min_replicas"), rs.getInt("max_replicas"), rs.getObject("pool_size", Integer.class),
                        List.copyOf(deps.getOrDefault(rs.getString("name"), new ArrayList<>())),
                        rs.getString("pool_size_env"), observability(rs.getString("request_metric"),
                                rs.getString("error_selector"), rs.getString("log_selector"), rs.getString("log_signals"))))
                .list();
    }

    /** 요청 메트릭이 없으면 관측 정의가 없는 서비스다 (null) */
    private Observability observability(String requestMetric, String errorSelector, String logSelector, String signals) {
        if (requestMetric == null) {
            return null;
        }
        try {
            return new Observability(requestMetric, errorSelector, logSelector,
                    signals == null ? Map.of() : json.readValue(signals, SIGNALS));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("services.log_signals가 JSON 객체가 아니다", e);
        }
    }

    /** 고객사의 서비스가 아니면 422. 메시지에 그 고객사의 서비스 목록을 담아 모델이 고쳐 부를 수 있게 한다. */
    public Entry require(Tenant tenant, String service) {
        List<Entry> entries = list(tenant.id());
        return entries.stream().filter(e -> e.name().equals(service)).findFirst().orElseThrow(() ->
                new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "'%s'는 %s 서비스가 아니다. %s 서비스: %s"
                        .formatted(service, tenant.name(), tenant.name(), entries.stream().map(Entry::name).toList())));
    }
}

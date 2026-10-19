package com.opslens.backend.change;

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

    public record Entry(String tenantId, String name, String runtime, String targetCluster, String targetNamespace,
            boolean stateful, int minReplicas, int maxReplicas, Integer poolSize, List<Dependency> dependencies) {}

    private final JdbcClient jdbc;

    public ServiceCatalog(JdbcClient jdbc) {
        this.jdbc = jdbc;
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
                        List.copyOf(deps.getOrDefault(rs.getString("name"), new ArrayList<>()))))
                .list();
    }

    /** 고객사의 서비스가 아니면 422. 메시지에 그 고객사의 서비스 목록을 담아 모델이 고쳐 부를 수 있게 한다. */
    public Entry require(Tenant tenant, String service) {
        List<Entry> entries = list(tenant.id());
        return entries.stream().filter(e -> e.name().equals(service)).findFirst().orElseThrow(() ->
                new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "'%s'는 %s 서비스가 아니다. %s 서비스: %s"
                        .formatted(service, tenant.name(), tenant.name(), entries.stream().map(Entry::name).toList())));
    }
}

package com.opslens.backend.impact;

import com.opslens.backend.auth.CurrentUser;
import com.opslens.backend.change.ServiceCatalog;
import java.time.Instant;
import java.util.*;
import org.springframework.stereotype.Service;

@Service
public class ImpactService {
    public record ServiceNode(String name, String runtime, String status, Double errorRate,
            Double p95Seconds, Double requestsPerSecond) {}
    public record Edge(String service, String dependencyId, String status, Double p95Seconds, Double callsPerSecond) {}
    public record Consumer(String service, List<String> path, boolean direct) {}
    public record DependencyNode(String id, String name, String kind, String status, List<Consumer> consumers) {}
    public record View(String tenantId, Instant observedAt, String window, List<ServiceNode> services,
            List<DependencyNode> dependencies, List<Edge> edges, List<String> warnings) {}
    private final ServiceCatalog catalog;
    private final ImpactMetrics metrics;

    public ImpactService(ServiceCatalog catalog, ImpactMetrics metrics) {
        this.catalog = catalog;
        this.metrics = metrics;
    }

    public View view(String tenant, CurrentUser user) {
        user.requireMspStaff();
        user.requireAccess(tenant);
        var entries = catalog.list(tenant);
        Instant at = Instant.now();
        var snapshot = entries.isEmpty() ? new ImpactMetrics.Snapshot(Map.of(), Map.of(), Map.of(), Map.of(), Map.of(), List.of())
                : metrics.read(tenant, at);
        var services = new ArrayList<ServiceNode>();
        var edges = new ArrayList<Edge>();
        var dependencies = new TreeMap<String, ServiceCatalog.Dependency>();
        for (var entry : entries) {
            var key = new ImpactMetrics.Key(entry.name(), "");
            Double rate = snapshot.requests().get(key), errors = snapshot.errors().get(key), p95 = snapshot.latency().get(key);
            Double errorRate = positive(rate) && valid(errors) ? errors / rate : null;
            if (errorRate != null && errorRate > 1) errorRate = null;
            services.add(new ServiceNode(entry.name(), entry.runtime(), serviceStatus(rate, errorRate, p95), errorRate, p95, rate));
            for (var dep : entry.dependencies()) {
                String id = dep.kind() + ":" + dep.name();
                dependencies.putIfAbsent(id, dep);
                var depKey = new ImpactMetrics.Key(entry.name(), dep.name());
                Double calls = snapshot.dependencyCalls().get(depKey), latency = snapshot.dependencyLatency().get(depKey);
                edges.add(new Edge(entry.name(), id, dependencyStatus(calls, latency), latency, calls));
            }
        }
        var nodes = dependencies.entrySet().stream().map(item -> new DependencyNode(item.getKey(), item.getValue().name(),
                item.getValue().kind(), aggregate(edges.stream().filter(e -> e.dependencyId().equals(item.getKey()))
                    .map(Edge::status).toList()), consumers(item.getKey(), entries))).toList();
        return new View(tenant, at, "최근 1분", List.copyOf(services), nodes,
                List.copyOf(edges), snapshot.warnings().stream().distinct().toList());
    }

    /** 역방향 탐색. internal_service 관계만 서비스 이름과 연결하며 순환은 한 번만 방문한다. */
    static List<Consumer> consumers(String dependencyId, List<ServiceCatalog.Entry> entries) {
        var found = new LinkedHashMap<String, Consumer>();
        var queue = new ArrayDeque<Consumer>();
        String root = dependencyId.substring(dependencyId.indexOf(':') + 1);
        for (var entry : entries) if (entry.dependencies().stream().anyMatch(d -> (d.kind() + ":" + d.name()).equals(dependencyId))) {
            if (dependencyId.equals("internal_service:" + entry.name())) continue;
            var consumer = new Consumer(entry.name(), List.of(entry.name(), root), true);
            found.put(entry.name(), consumer);
            queue.add(consumer);
        }
        while (!queue.isEmpty()) {
            var current = queue.remove();
            for (var entry : entries) {
                if (found.containsKey(entry.name()) || dependencyId.equals("internal_service:" + entry.name())) continue;
                if (entry.dependencies().stream().noneMatch(d -> "internal_service".equals(d.kind()) && current.service().equals(d.name()))) continue;
                var path = new ArrayList<String>();
                path.add(entry.name()); path.addAll(current.path());
                var consumer = new Consumer(entry.name(), List.copyOf(path), false);
                found.put(entry.name(), consumer); queue.add(consumer);
            }
        }
        return List.copyOf(found.values());
    }

    static boolean valid(Double n) { return n != null && Double.isFinite(n) && n >= 0; }
    static boolean positive(Double n) { return valid(n) && n > 0; }
    static String serviceStatus(Double rate, Double errorRate, Double p95) {
        if (!positive(rate)) return "UNKNOWN";
        if ((valid(errorRate) && errorRate >= 0.05) || (valid(p95) && p95 >= 1)) return "ABNORMAL";
        return valid(errorRate) && valid(p95) ? "HEALTHY" : "UNKNOWN";
    }
    static String dependencyStatus(Double calls, Double p95) {
        if (!positive(calls) || !valid(p95)) return "UNKNOWN";
        return p95 >= 3 ? "HIGH_LATENCY" : p95 >= 0.3 ? "DEGRADED" : "HEALTHY";
    }
    static String aggregate(List<String> states) {
        if (states.contains("HIGH_LATENCY")) return "HIGH_LATENCY";
        if (states.contains("DEGRADED")) return "DEGRADED";
        return states.isEmpty() || states.contains("UNKNOWN") ? "UNKNOWN" : "HEALTHY";
    }
}

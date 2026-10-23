package com.opslens.backend.change;

import com.opslens.backend.tenant.Tenant;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

/**
 * Agent가 정한 조치 의도를 검사하고 반영한다 (SPEC-fault-based-actions 3장의 ②·④층, ③층 계약 검사는 호출한 쪽).
 *   ② 구조 제약: 서비스 카탈로그로 계산한 안전 상한, 상태 저장 여부, 실행 환경의 지원 조치
 *   ④ 반영 방식: 실행 환경(runtime)으로 어댑터를 고르고, 원하는 상태 변경은 GITOPS 고객사면 PR로 바꾼다
 *     restart_pods        일회성 조치. 원하는 상태가 그대로라 GITOPS 고객사도 어댑터로 바로 실행한다
 *     scale_deployment    원하는 상태 변경. GITOPS면 PR, 아니면 어댑터
 *     rollback_deployment 원하는 상태 변경. 장애 시작 전 마지막 변경을 GITOPS면 되돌리기 PR로, 아니면 어댑터로 되돌린다
 *     adjust_autoscaler   원하는 상태 변경. HPA 최대치를 안전 상한까지 올린다. GITOPS면 hpa.yaml PR (스펙 5.4.2)
 *   HPA가 관리하는 서비스는 레플리카를 직접 바꾸지 않는다(scale_deployment 거부). HPA가 곧 덮어쓰기 때문이다
 * Agent와 MCP 도구는 반영 방식을 모른다. 구조 제약은 Agent도 같은 계산값(inventory)으로 미리 확인하고, 여기서 다시 막는다.
 */
@Service
public class ChangeService {

    public static final Set<String> ACTIONS = Set.of("restart_pods", "scale_deployment", "rollback_deployment",
            "adjust_autoscaler");
    public static final String EXECUTED = "EXECUTED";
    public static final String PR_CREATED = "PR_CREATED";

    /** 상태 저장 서비스에 하지 않는 조치 */
    private static final Set<String> STATEFUL_UNSAFE = Set.of("restart_pods", "scale_deployment");

    private final ServiceCatalog catalog;
    private final Map<String, RuntimeAdapter> adapters;
    private final GitOpsRepository gitops;

    public ChangeService(ServiceCatalog catalog, List<RuntimeAdapter> adapters, GitOpsRepository gitops) {
        this.catalog = catalog;
        this.adapters = adapters.stream().collect(Collectors.toMap(RuntimeAdapter::runtime, Function.identity()));
        this.gitops = gitops;
    }

    public Map<String, Object> execute(Tenant tenant, String action, String service, Integer replicas, String reason,
            OffsetDateTime incidentStartedAt) {
        if (!ACTIONS.contains(action)) {
            throw unprocessable("알 수 없는 조치 '%s'. 가능한 조치: %s".formatted(action, new TreeSet<>(ACTIONS)));
        }
        ServiceCatalog.Entry entry = catalog.require(tenant, service);
        RuntimeAdapter adapter = adapter(entry);
        if (!adapter.supportedActions().contains(action)) {
            throw unprocessable("%s 실행 환경(%s)은 %s를 지원하지 않는다. 에스컬레이션한다"
                    .formatted(service, entry.runtime(), action));
        }
        if (entry.stateful() && STATEFUL_UNSAFE.contains(action)) {
            throw unprocessable("%s는 상태 저장 서비스라 %s를 하지 않는다. 에스컬레이션한다".formatted(service, action));
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("action", action);
        result.put("service", service);
        result.put("runtime", entry.runtime());
        result.put("changeMode", tenant.changeMode());
        switch (action) {
            case "restart_pods" -> {
                RuntimeAdapter.Restart restart = adapter.restart(tenant, entry);
                result.put("status", EXECUTED);
                result.put("restartedPods", restart.restarted());
                result.put("restartMode", restart.rolling() ? "ROLLING" : "ALL_AT_ONCE");
            }
            case "scale_deployment" -> {
                RuntimeAdapter.Workload workload = adapter.status(tenant, entry);
                if (workload.autoscaler() != null) {
                    RuntimeAdapter.Autoscaler hpa = workload.autoscaler();
                    throw unprocessable(("%s의 레플리카는 HPA(%s, %d~%d)가 관리해 직접 바꾸지 않는다. "
                            + "HPA 최대치가 모자라면 adjust_autoscaler로 안전 상한 %d까지 올린다")
                            .formatted(service, hpa.name(), hpa.minReplicas(), hpa.maxReplicas(),
                                    safeMaxReplicas(entry, poolSize(entry, workload))));
                }
                requireReplicas(entry, replicas, workload);
                result.put("replicas", replicas);
                if (tenant.gitops()) {
                    putPullRequest(result, gitops.scale(tenant, service, replicas, reason));
                } else {
                    adapter.scale(tenant, entry, replicas);
                    result.put("status", EXECUTED);
                }
            }
            case "adjust_autoscaler" -> {
                RuntimeAdapter.Workload workload = adapter.status(tenant, entry);
                RuntimeAdapter.Autoscaler hpa = requireAutoscalerMax(entry, replicas, workload);
                result.put("autoscaler", hpa.name());
                result.put("previousMaxReplicas", hpa.maxReplicas());
                result.put("maxReplicas", replicas);
                if (tenant.gitops()) {
                    putPullRequest(result, gitops.setAutoscalerMax(tenant, service, replicas, reason));
                } else {
                    adapter.setAutoscalerMax(tenant, entry, hpa, replicas);
                    result.put("status", EXECUTED);
                }
            }
            case "rollback_deployment" -> {
                if (tenant.gitops()) {
                    putPullRequest(result, gitops.rollback(tenant, service, reason, incidentStartedAt));
                } else {
                    result.put("status", EXECUTED);
                    result.put("rolledBackTo", adapter.rollback(tenant, entry, incidentStartedAt));
                }
            }
            // ACTIONS에 조치를 더하고 여기를 빠뜨리면 다른 조치로 실행하지 않고 막는다
            default -> throw new IllegalStateException("조치 '%s'의 반영 방법이 없다".formatted(action));
        }
        return result;
    }

    /** 최근 배포·설정 변경. GITOPS는 설정 저장소 커밋, 아니면 실행 환경의 배포 이력. */
    public Map<String, Object> recentChanges(Tenant tenant, String service) {
        ServiceCatalog.Entry entry = catalog.require(tenant, service);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("service", service);
        result.put("changeMode", tenant.changeMode());
        if (tenant.gitops()) {
            result.put("source", "설정 저장소 " + GitOpsRepository.manifest(tenant, service));
            result.put("commits", gitops.recentCommits(tenant, service, 3));
        } else {
            result.put("source", entry.runtime() + " 배포 이력");
            result.put("revisions", adapter(entry).history(tenant, entry).stream().limit(3).toList());
        }
        return result;
    }

    /**
     * 서비스 카탈로그 + 실행 중인 상태 + 계산값. Agent가 조치의 구조 검토(②층)에 쓴다.
     * 실행 환경에 닿지 않거나 어댑터가 없으면 실행 상태 없이 카탈로그와 그 이유만 돌려준다.
     */
    public List<Map<String, Object>> inventory(Tenant tenant) {
        return catalog.list(tenant.id()).stream().map(entry -> {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", entry.name());
            item.put("runtime", entry.runtime());
            item.put("target", entry.targetNamespace() == null ? entry.targetCluster()
                    : entry.targetCluster() + "/" + entry.targetNamespace());
            item.put("stateful", entry.stateful());
            item.put("contractReplicas", Map.of("min", entry.minReplicas(), "max", entry.maxReplicas()));
            item.put("dependencies", entry.dependencies());
            item.put("observability", entry.observability());

            Optional<RuntimeAdapter> adapter = Optional.ofNullable(adapters.get(entry.runtime()));
            RuntimeAdapter.Workload workload = null;
            if (adapter.isEmpty()) {
                item.put("runtimeSupported", false);
            } else {
                try {
                    workload = adapter.get().status(tenant, entry);
                    item.put("replicas", workload.replicas());
                    item.put("readyReplicas", workload.readyReplicas());
                    item.put("image", workload.image());
                } catch (ResponseStatusException e) {
                    item.put("statusError", e.getReason());
                }
            }
            Integer pool = poolSize(entry, workload);
            item.put("poolSize", pool);
            item.put("safeMaxReplicas", safeMaxReplicas(entry, pool));
            item.put("zeroDowntimeRestart", workload != null && !entry.stateful() && workload.replicas() >= 2);
            RuntimeAdapter.Autoscaler hpa = workload == null ? null : workload.autoscaler();
            if (hpa != null) {
                Map<String, Object> autoscaling = new LinkedHashMap<>();
                autoscaling.put("name", hpa.name());
                autoscaling.put("minReplicas", hpa.minReplicas());
                autoscaling.put("maxReplicas", hpa.maxReplicas());
                autoscaling.put("currentReplicas", hpa.currentReplicas());
                autoscaling.put("desiredReplicas", hpa.desiredReplicas());
                autoscaling.put("targetCpuPercent", hpa.targetCpuPercent());
                autoscaling.put("currentCpuPercent", hpa.currentCpuPercent());
                autoscaling.put("atMax", hpa.atMax());
                autoscaling.put("canRaiseMax", hpa.maxReplicas() < safeMaxReplicas(entry, pool));
                item.put("autoscaling", autoscaling);
            }
            item.put("supportedActions", supportedActions(tenant, entry, adapter, hpa != null));
            return item;
        }).toList();
    }

    // ── 구조 제약 계산 (SPEC 4.1) ──────────────────────────────

    /** min(계약 상한, ⌊DB 최대 연결 × 여유율 ÷ 풀 크기⌋). DB 의존이 없거나 풀 크기를 모르면 계약 상한이다. */
    static int safeMaxReplicas(ServiceCatalog.Entry entry, Integer poolSize) {
        int max = entry.maxReplicas();
        if (poolSize == null || poolSize <= 0) {
            return max;
        }
        for (ServiceCatalog.Dependency dep : entry.dependencies()) {
            if (dep.database() && dep.maxConnections() != null) {
                double usable = dep.maxConnections() * dep.connectionHeadroom().doubleValue();
                max = Math.min(max, (int) Math.floor(usable / poolSize));
            }
        }
        return max;
    }

    /** 실행 환경이 지원하는 조치 ∩ 계약 허용 조치. 상태 저장이면 재시작·확장을 뺀다. HPA가 있으면 직접 확장 대신 최대치 조정이다. */
    private static List<String> supportedActions(Tenant tenant, ServiceCatalog.Entry entry,
            Optional<RuntimeAdapter> adapter, boolean autoscaled) {
        return adapter.map(RuntimeAdapter::supportedActions).orElse(Set.of()).stream()
                .filter(tenant.allowedActions()::contains)
                .filter(a -> !(entry.stateful() && STATEFUL_UNSAFE.contains(a)))
                .filter(a -> !(autoscaled ? "scale_deployment" : "adjust_autoscaler").equals(a))
                .sorted().toList();
    }

    /** 커넥션 풀 크기. 카탈로그가 정한 환경변수로 배포 설정의 실제 값을 먼저 읽고, 없으면 카탈로그 기준값을 쓴다 */
    private static Integer poolSize(ServiceCatalog.Entry entry, RuntimeAdapter.Workload workload) {
        String env = entry.poolSizeEnv();
        if (env != null && workload != null && workload.env().containsKey(env)) {
            try {
                return Integer.parseInt(workload.env().get(env));
            } catch (NumberFormatException ignored) {
                // 배포 설정이 숫자가 아니면 카탈로그 기준값을 쓴다
            }
        }
        return entry.poolSize();
    }

    private static void requireReplicas(ServiceCatalog.Entry entry, Integer replicas, RuntimeAdapter.Workload workload) {
        int safeMax = safeMaxReplicas(entry, poolSize(entry, workload));
        if (replicas == null || replicas < entry.minReplicas() || replicas > safeMax) {
            throw unprocessable(("scale_deployment의 replicas는 %d~%d 사이여야 한다 (요청 %s). 상한은 계약 %d와 "
                    + "DB 연결 한도로 계산한 안전 상한이다. 이미 상한이면 에스컬레이션한다")
                    .formatted(entry.minReplicas(), safeMax, replicas, entry.maxReplicas()));
        }
    }

    private static RuntimeAdapter.Autoscaler requireAutoscalerMax(ServiceCatalog.Entry entry, Integer maxReplicas,
            RuntimeAdapter.Workload workload) {
        RuntimeAdapter.Autoscaler hpa = workload.autoscaler();
        if (hpa == null) {
            throw unprocessable("%s에는 HPA가 없다. 레플리카를 바꾸려면 scale_deployment를 쓴다".formatted(entry.name()));
        }
        int safeMax = safeMaxReplicas(entry, poolSize(entry, workload));
        if (hpa.maxReplicas() >= safeMax) {
            throw unprocessable(("%s HPA 최대치 %d가 이미 안전 상한 %d다. 더 올리면 DB 연결이 모자란다. "
                    + "에스컬레이션한다 (ACTION_BLOCKED, STRUCTURE)").formatted(entry.name(), hpa.maxReplicas(), safeMax));
        }
        if (maxReplicas == null || maxReplicas <= hpa.maxReplicas() || maxReplicas > safeMax) {
            throw unprocessable("adjust_autoscaler의 최대치는 %d보다 크고 안전 상한 %d 이하여야 한다 (요청 %s)"
                    .formatted(hpa.maxReplicas(), safeMax, maxReplicas));
        }
        return hpa;
    }

    private RuntimeAdapter adapter(ServiceCatalog.Entry entry) {
        RuntimeAdapter adapter = adapters.get(entry.runtime());
        if (adapter == null) {
            throw unprocessable("%s의 실행 환경(%s)은 OpsLens가 지원하지 않아 조치할 수 없다. 지원: %s. 에스컬레이션한다"
                    .formatted(entry.name(), entry.runtime(), new TreeSet<>(adapters.keySet())));
        }
        return adapter;
    }

    private static ResponseStatusException unprocessable(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }

    private static void putPullRequest(Map<String, Object> result, GitOpsRepository.PullRequest pr) {
        result.put("status", PR_CREATED);
        result.put("prNumber", pr.number());
        result.put("prUrl", pr.url());
        result.put("files", pr.files());
        result.put("next", "PR이 병합되고 ArgoCD 동기화가 끝나야 반영된다");
    }
}

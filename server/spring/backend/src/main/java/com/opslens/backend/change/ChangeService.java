package com.opslens.backend.change;

import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

/**
 * Agent가 정한 조치 의도를 고객사의 반영 방식으로 실행한다 (ADR 0001 D3·D4). Agent와 MCP 도구는 반영 방식을 모른다.
 *   restart_pods        일회성 조치. 원하는 상태가 그대로라 GITOPS 고객사도 API로 바로 실행한다
 *   scale_deployment    원하는 상태 변경. GITOPS면 PR, DIRECT면 API
 *   rollback_deployment 원하는 상태 변경. GITOPS면 되돌리기 PR, DIRECT면 직전 ReplicaSet으로 되돌린다
 */
@Service
public class ChangeService {

    public static final Set<String> ACTIONS = Set.of("restart_pods", "scale_deployment", "rollback_deployment");
    public static final String EXECUTED = "EXECUTED";
    public static final String PR_CREATED = "PR_CREATED";

    private static final int MAX_REPLICAS = 10;

    private final TenantRepository tenants;
    private final KubernetesClient kubernetes;
    private final GitOpsRepository gitops;

    public ChangeService(TenantRepository tenants, KubernetesClient kubernetes, GitOpsRepository gitops) {
        this.tenants = tenants;
        this.kubernetes = kubernetes;
        this.gitops = gitops;
    }

    public Map<String, Object> execute(Tenant tenant, String action, String service, Integer replicas, String reason) {
        requireService(tenant, service);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("action", action);
        result.put("service", service);
        result.put("changeMode", tenant.changeMode());
        switch (action) {
            case "restart_pods" -> {
                result.put("status", EXECUTED);
                result.put("restartedPods", kubernetes.restartPods(tenant, service));
            }
            case "scale_deployment" -> {
                if (replicas == null || replicas < 1 || replicas > MAX_REPLICAS) {
                    throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                            "scale_deployment에는 replicas(1~%d)가 필요하다".formatted(MAX_REPLICAS));
                }
                result.put("replicas", replicas);
                if (tenant.gitops()) {
                    putPullRequest(result, gitops.scale(tenant, service, replicas, reason));
                } else {
                    kubernetes.scale(tenant, service, replicas);
                    result.put("status", EXECUTED);
                }
            }
            case "rollback_deployment" -> {
                if (tenant.gitops()) {
                    putPullRequest(result, gitops.rollback(tenant, service, reason));
                } else {
                    result.put("status", EXECUTED);
                    result.put("rolledBackTo", kubernetes.rollback(tenant, service));
                }
            }
            default -> throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "알 수 없는 조치 '%s'. 가능한 조치: %s".formatted(action, ACTIONS));
        }
        return result;
    }

    /** 최근 배포·설정 변경. GITOPS는 설정 저장소 커밋, DIRECT는 Deployment의 ReplicaSet 이력. */
    public Map<String, Object> recentChanges(Tenant tenant, String service) {
        requireService(tenant, service);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("service", service);
        result.put("changeMode", tenant.changeMode());
        if (tenant.gitops()) {
            result.put("source", "설정 저장소 " + tenant.gitopsPath());
            result.put("commits", gitops.recentCommits(tenant, 3));
        } else {
            result.put("source", "Deployment 배포 이력");
            result.put("revisions", kubernetes.history(tenant, service).stream().limit(3).toList());
        }
        return result;
    }

    /** 서비스 목록에 현재 레플리카 수와 이미지를 붙인다. 클러스터에 닿지 않으면 목록만 돌려준다. */
    public List<Map<String, Object>> inventory(Tenant tenant) {
        return tenants.services(tenant.id()).stream().map(s -> {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", s.name());
            item.put("dependsOn", s.dependsOn());
            try {
                KubernetesClient.Deployment d = kubernetes.deployment(tenant, s.name());
                item.put("replicas", d.replicas());
                item.put("readyReplicas", d.readyReplicas());
                item.put("image", d.image());
            } catch (ResponseStatusException e) {
                item.put("deploymentError", e.getReason());
            }
            return item;
        }).toList();
    }

    private void requireService(Tenant tenant, String service) {
        List<String> names = tenants.services(tenant.id()).stream().map(TenantRepository.Service::name).toList();
        if (!names.contains(service)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "'%s'는 %s 서비스가 아니다. %s 서비스: %s".formatted(service, tenant.name(), tenant.name(), names));
        }
    }

    private static void putPullRequest(Map<String, Object> result, GitOpsRepository.PullRequest pr) {
        result.put("status", PR_CREATED);
        result.put("prNumber", pr.number());
        result.put("prUrl", pr.url());
        result.put("files", pr.files());
        result.put("next", "PR이 병합되고 ArgoCD 동기화가 끝나야 반영된다");
    }
}

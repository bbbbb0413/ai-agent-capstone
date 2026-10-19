package com.opslens.backend.change;

import com.opslens.backend.tenant.Tenant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 고객사 서비스의 실행 환경(카탈로그의 runtime)마다 조치를 실제로 반영하는 어댑터 (SPEC-fault-based-actions 5.2).
 * 지금은 Kubernetes만 구현한다. Docker·VM·ECS는 같은 인터페이스로 추가한다(스펙 5.5). 어댑터가 없는 runtime의 조치는
 * ChangeService가 "지원하지 않는 실행 환경"으로 거부하고, Agent는 에스컬레이션한다.
 * 설정 저장소 PR(GITOPS)은 실행 환경이 아니라 고객사 운영 방식이라 여기가 아니라 GitOpsRepository가 맡는다.
 */
public interface RuntimeAdapter {

    /** 실행 중인 상태. env는 값이 직접 적힌 환경변수만 담는다(Secret 참조는 내보내지 않는다). */
    record Workload(String name, int replicas, int readyReplicas, String image, Map<String, String> env) {}

    record Revision(int revision, String image, Map<String, String> env, String createdAt) {}

    /** rolling이면 하나씩 재시작하고 새 파드가 Ready가 된 뒤 다음으로 넘어갔다. */
    record Restart(List<String> restarted, boolean rolling) {}

    String runtime();

    /** 이 실행 환경이 지원하는 조치. 카탈로그 계산값 supportedActions의 한 축이다. */
    Set<String> supportedActions();

    Workload status(Tenant tenant, ServiceCatalog.Entry service);

    Restart restart(Tenant tenant, ServiceCatalog.Entry service);

    void scale(Tenant tenant, ServiceCatalog.Entry service, int replicas);

    Revision rollback(Tenant tenant, ServiceCatalog.Entry service, OffsetDateTime incidentStartedAt);

    List<Revision> history(Tenant tenant, ServiceCatalog.Entry service);
}

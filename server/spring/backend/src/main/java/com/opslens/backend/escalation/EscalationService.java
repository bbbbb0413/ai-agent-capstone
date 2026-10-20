package com.opslens.backend.escalation;

import com.opslens.backend.change.ServiceCatalog;
import com.opslens.backend.incident.Incident;
import com.opslens.backend.tenant.Tenant;
import com.opslens.backend.tenant.TenantRepository;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.StringJoiner;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

/**
 * 에스컬레이션을 발송 직전까지 처리한다: 요청을 검사하고, 받는 사람을 정하고, 메시지를 만들어 발송 대기열에 넣는다.
 * 실제 발송(메신저·전화·메일)은 하지 않는다. 발송기를 붙이면 대기열(escalations, status QUEUED)을 읽어 보낸다.
 *
 * 받는 사람 (공통 런북 RB-STD-04~07):
 *   항상            담당 엔지니어와 MSP 관리자
 *   ACTION_BLOCKED  + 고객사 담당. 계약·구조상 할 수 없는 조치의 승인이나 판단을 요청한다
 *   DEPENDENCY      + 원인인 의존 대상의 소유자 (서비스 카탈로그). 서드파티 외부 API는 공지 대상이지 호출 대상이 아니다
 */
@Service
public class EscalationService {

    public record Request(String category, String reason, String service, String requiredAction, String blockedBy,
            String dependency) {}

    /** 분류와 화면·메시지에 쓰는 이름 */
    public static final Map<String, String> CATEGORIES = categories();
    public static final Map<String, String> BLOCKED_BY = Map.of(
            "CONTRACT", "계약상 허용되지 않는 조치", "STRUCTURE", "구조상 안전하지 않은 조치(상한·상태 저장)",
            "RUNTIME", "OpsLens가 지원하지 않는 실행 환경");

    private final EscalationRepository escalations;
    private final TenantRepository tenants;
    private final ServiceCatalog catalog;

    public EscalationService(EscalationRepository escalations, TenantRepository tenants, ServiceCatalog catalog) {
        this.escalations = escalations;
        this.tenants = tenants;
        this.catalog = catalog;
    }

    /** 발송 대기열에 넣은(또는 이미 대기 중인) 건과 duplicate 여부를 돌려준다. */
    public Map<String, Object> queue(Tenant tenant, Incident incident, Request raw, String traceId) {
        Request request = validate(tenant, incident, raw);
        Optional<Escalation> existing = escalations.findQueued(incident.id(), request);
        Escalation escalation;
        if (existing.isPresent()) {
            escalation = existing.get();
        } else {
            List<Escalation.Recipient> recipients = recipients(tenant, request);
            String message = message(tenant, incident, request);
            long id = escalations.insert(tenant.id(), incident.id(), request, recipients, message, traceId);
            escalation = new Escalation(id, tenant.id(), incident.id(), request.category(), request.reason(),
                    request.service(), request.requiredAction(), request.blockedBy(), request.dependency(),
                    recipients, message, Escalation.QUEUED, null, null);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("escalationId", escalation.id());
        result.put("status", escalation.status());
        result.put("duplicate", existing.isPresent());
        result.put("category", escalation.category());
        result.put("recipients", escalation.recipients());
        result.put("message", escalation.message());
        result.put("delivery", "발송 대기열에 저장했다. 실제 발송(메신저·전화·메일)은 아직 연결되지 않았다");
        return result;
    }

    public List<Escalation> list(String tenantId, long incidentId) {
        return escalations.listForIncident(tenantId, incidentId);
    }

    // ── 검사 ──────────────────────────────────────────────────

    private Request validate(Tenant tenant, Incident incident, Request r) {
        String category = r.category() == null ? null : r.category().toUpperCase();
        if (!CATEGORIES.containsKey(category)) {
            throw unprocessable("에스컬레이션 분류 '%s'를 모른다. 가능한 분류: %s".formatted(r.category(), CATEGORIES));
        }
        String service = r.service() != null ? r.service() : incident.service();
        ServiceCatalog.Entry entry = service == null ? null : catalog.require(tenant, service);
        String blockedBy = r.blockedBy() == null ? null : r.blockedBy().toUpperCase();
        if ("ACTION_BLOCKED".equals(category)) {
            if (r.requiredAction() == null || r.requiredAction().isBlank()) {
                throw unprocessable("ACTION_BLOCKED는 required_action(필요하지만 할 수 없는 조치)이 필요하다");
            }
            if (!BLOCKED_BY.containsKey(blockedBy)) {
                throw unprocessable("ACTION_BLOCKED는 blocked_by가 필요하다. 가능한 값: %s".formatted(BLOCKED_BY));
            }
        }
        if ("DEPENDENCY".equals(category)) {
            if (entry == null || r.dependency() == null) {
                throw unprocessable("DEPENDENCY는 service와 dependency(원인인 의존 대상 이름)가 필요하다");
            }
            ServiceCatalog.Dependency dep = dependency(entry, r.dependency()).orElseThrow(() -> unprocessable(
                    "'%s'는 %s의 의존 대상이 아니다. 의존 대상: %s".formatted(r.dependency(), entry.name(),
                            entry.dependencies().stream().map(ServiceCatalog.Dependency::name).toList())));
            if ("third_party".equals(dep.owner())) {
                throw unprocessable(("%s는 서드파티 소유라 MSP가 호출할 담당이 없다. 에스컬레이션 대신 고객 공지를 한다 "
                        + "(RB-STD-04)").formatted(dep.name()));
            }
        }
        return new Request(category, r.reason(), service, r.requiredAction(), blockedBy, r.dependency());
    }

    // ── 받는 사람과 메시지 ─────────────────────────────────────

    List<Escalation.Recipient> recipients(Tenant tenant, Request request) {
        List<Escalation.Recipient> recipients = new ArrayList<>();
        for (TenantRepository.Contact c : tenants.oncall(tenant.id())) {
            recipients.add(new Escalation.Recipient("MSP_ONCALL", c.username(), c.role()));
        }
        if ("ACTION_BLOCKED".equals(request.category())) {
            String contact = tenants.customerContact(tenant.id()).orElse(tenant.name() + " 담당");
            recipients.add(new Escalation.Recipient("CUSTOMER", contact, tenant.name()));
        }
        if ("DEPENDENCY".equals(request.category())) {
            catalog.list(tenant.id()).stream().filter(e -> e.name().equals(request.service())).findFirst()
                    .flatMap(e -> dependency(e, request.dependency()))
                    .ifPresent(d -> recipients.add(new Escalation.Recipient("DEPENDENCY_OWNER",
                            d.ownerContact() != null ? d.ownerContact() : d.owner(), d.name())));
        }
        return recipients;
    }

    static String message(Tenant tenant, Incident incident, Request request) {
        StringJoiner lines = new StringJoiner("\n");
        lines.add("[OpsLens 에스컬레이션] %s 인시던트 #%d (%s%s)".formatted(tenant.name(), incident.id(),
                incident.severity(), request.service() == null ? "" : ", " + request.service()));
        lines.add("장애: " + incident.title());
        lines.add("분류: " + CATEGORIES.get(request.category()));
        lines.add("이유: " + request.reason());
        if (request.requiredAction() != null) {
            lines.add("필요한 조치: " + request.requiredAction());
        }
        if (request.blockedBy() != null) {
            lines.add("할 수 없는 이유: " + BLOCKED_BY.get(request.blockedBy()));
        }
        if (request.dependency() != null) {
            lines.add("원인 의존 대상: " + request.dependency());
        }
        lines.add("OpsLens에서 인시던트 #%d의 작업과 보고서를 확인한다".formatted(incident.id()));
        return lines.toString();
    }

    private static Optional<ServiceCatalog.Dependency> dependency(ServiceCatalog.Entry entry, String name) {
        return entry.dependencies().stream().filter(d -> d.name().equals(name)).findFirst();
    }

    private static Map<String, String> categories() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("P1", "P1 장애 발생");
        m.put("NOT_RECOVERED", "조치 후에도 회복되지 않음");
        m.put("CHANGE_TIMEOUT", "조치 PR이 기한 안에 병합되지 않음");
        m.put("ACTION_BLOCKED", "필요한 조치를 계약·구조상 할 수 없음");
        m.put("DEPENDENCY", "원인이 다른 팀 소유의 의존 대상");
        m.put("CAUSE_UNKNOWN", "원인 미확정");
        return Collections.unmodifiableMap(m);
    }

    private static ResponseStatusException unprocessable(String message) {
        return new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, message);
    }
}

package com.opslens.backend.escalation;

import java.time.OffsetDateTime;
import java.util.List;

/** 발송 대기열에 들어간 에스컬레이션 한 건. status는 QUEUED에서 시작한다 (실제 발송은 아직 없다). */
public record Escalation(long id, String tenantId, long incidentId, String category, String reason, String service,
        String requiredAction, String blockedBy, String dependency, List<Recipient> recipients, String message,
        String status, OffsetDateTime createdAt, OffsetDateTime sentAt) {

    public static final String QUEUED = "QUEUED";

    /**
     * 받는 사람. kind는 왜 이 사람이 받는지다.
     *   MSP_ONCALL       담당 엔지니어와 MSP 관리자 (항상)
     *   CUSTOMER         고객사 담당 (계약·구조상 할 수 없는 조치의 승인·판단 요청)
     *   DEPENDENCY_OWNER 원인인 의존 대상의 소유자 (서비스 카탈로그의 owner_contact)
     */
    public record Recipient(String kind, String name, String role) {}
}

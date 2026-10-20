"""business-mcp: 업무 기능을 Agent가 호출하는 도구로 노출한다.

- Spring Boot `/internal/tenants/{tenant}/**`만 호출하고 DB에는 접근하지 않는다.
- 업무 규칙(SLA 계산, 조치 허용 여부, 고객사 소유권)은 Spring Boot에만 있다. 여기서 복제하지 않는다.
- 고객사는 agent가 요청 헤더(X-Tenant-Id)로 넘긴다. 모델은 도구 인자로 고객사를 바꿀 수 없다.
- 실패는 ToolError로 돌려준다. Spring Boot의 오류 메시지에 재시도에 필요한 정보가 들어 있다.
"""

import os
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from starlette.middleware import Middleware

from business_mcp.auth import ServiceTokenMiddleware

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")

READ = {"readOnlyHint": True, "destructiveHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": True}

mcp = FastMCP("business")


async def _call(method: str, path: str, timeout: float = 10, **kwargs: Any) -> Any:
    headers = get_http_headers()
    tenant = headers.get("x-tenant-id")
    if not tenant:
        raise ToolError("고객사 컨텍스트(X-Tenant-Id)가 없다. agent의 MCP 연결 설정을 확인한다")
    forward = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": tenant,
        "X-Trace-Id": headers.get("x-trace-id", ""),
    }
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=timeout) as client:
            response = await client.request(method, f"/internal/tenants/{tenant}{path}", headers=forward, **kwargs)
    except httpx.HTTPError as e:
        raise ToolError(f"backend에 연결하지 못했다({type(e).__name__}). 잠시 후 다시 시도한다") from e
    if response.is_error:
        try:
            message = response.json().get("message") or response.text
        except ValueError:
            message = response.text
        raise ToolError(f"{response.status_code}: {message}")
    return response.json()


# ── 조회 도구 ──────────────────────────────────────────────────


@mcp.tool(annotations=READ)
async def get_tenant_profile() -> dict[str, Any]:
    """현재 고객사의 이름, SLA 등급(GOLD|SILVER), 가용성 목표(%), 응답 기한(분), 계약상 허용 조치 목록을 돌려준다."""
    return await _call("GET", "")


@mcp.tool(annotations=READ)
async def get_sla_terms(incident_id: int) -> dict[str, Any]:
    """인시던트의 SLA 조건을 돌려준다. responseDeadline(응답 기한 시각)과 remainingMinutes(남은 분)는
    backend가 계약 기준으로 계산한 값이므로 그대로 쓴다."""
    return await _call("GET", "/sla", params={"incidentId": incident_id})


@mcp.tool(annotations=READ)
async def list_incidents(status: Literal["OPEN", "MITIGATED", "RESOLVED"] | None = None) -> list[dict[str, Any]]:
    """현재 고객사의 최근 인시던트 목록(최대 100건, 최신순). status로 거를 수 있다. 과거 유사 장애를 찾을 때 쓴다."""
    return await _call("GET", "/incidents", params={"status": status} if status else None)


@mcp.tool(annotations=READ)
async def get_oncall_contacts() -> list[dict[str, Any]]:
    """에스컬레이션 대상(담당 엔지니어, MSP 관리자)의 username과 역할을 돌려준다."""
    return await _call("GET", "/oncall")


@mcp.tool(annotations=READ)
async def get_service_inventory() -> list[dict[str, Any]]:
    """현재 고객사 서비스의 카탈로그(시스템 구조)와 실행 상태를 돌려준다. 조치를 고를 때 구조 검토에 쓴다.
    - runtime: 실행 환경(kubernetes 등). runtimeSupported가 false면 OpsLens가 조치할 수 없다
    - dependencies: 의존 대상의 종류(database, external_api), 소유(tenant, third_party), 연락처, DB 최대 연결 수
    - contractReplicas: 계약상 레플리카 범위, replicas/readyReplicas: 지금 값, poolSize: 커넥션 풀 크기
    - safeMaxReplicas: 확장 안전 상한(계약 상한과 DB 연결 한도 중 작은 값). 이보다 크게 확장하면 거부된다
    - zeroDowntimeRestart: true면 하나씩 재시작해 중단이 없고, false면 재시작하는 동안 서비스가 멈춘다
    - supportedActions: 지금 이 서비스에 할 수 있는 조치(실행 환경 지원 ∩ 계약 허용)"""
    return await _call("GET", "/services")


@mcp.tool(annotations=READ)
async def list_recent_changes(service: str) -> dict[str, Any]:
    """서비스의 최근 배포·설정 변경 3건(최신순). 장애가 직전 변경과 관련 있는지 볼 때 쓴다.
    설정 저장소로 운영하는 고객사는 커밋(시각, 메시지, 변경 내용 일부)을, 직접 운영 고객사는 배포 이력
    (revision, 이미지, 환경변수, 생성 시각)을 돌려준다."""
    return await _call("GET", "/changes", params={"service": service})


# ── 변경 도구 (destructiveHint: agent Harness가 실행 전에 사람의 승인을 받는다) ──


@mcp.tool(annotations=WRITE)
async def create_incident(service: str, title: str, severity: Literal["P1", "P2", "P3"]) -> dict[str, Any]:
    """알림 없이 발견한 장애를 인시던트로 등록한다. 새 incidentId를 돌려준다."""
    return await _call("POST", "/incidents", json={"service": service, "title": title, "severity": severity})


@mcp.tool(annotations=WRITE)
async def escalate_incident(
    incident_id: int,
    category: Literal["P1", "NOT_RECOVERED", "CHANGE_TIMEOUT", "ACTION_BLOCKED", "DEPENDENCY", "CAUSE_UNKNOWN"],
    reason: str,
    service: str | None = None,
    required_action: Literal["restart_pods", "scale_deployment", "rollback_deployment"] | None = None,
    blocked_by: Literal["CONTRACT", "STRUCTURE", "RUNTIME"] | None = None,
    dependency: str | None = None,
) -> dict[str, Any]:
    """사람에게 장애를 넘긴다(담당자 호출). 받는 사람과 메시지는 backend가 정해 발송 대기열에 넣는다.
    - ACTION_BLOCKED: 필요한 조치를 계약(CONTRACT)·구조(STRUCTURE: 안전 상한, 상태 저장)·실행 환경(RUNTIME)상
      할 수 없다 (RB-STD-06). required_action과 blocked_by가 필요하다. 고객사 담당도 받는다
    - DEPENDENCY: 원인이 고객사 다른 팀 소유의 의존 대상이다 (RB-STD-04·05). dependency에 get_service_inventory의
      의존 대상 이름을 넣는다. 그 소유자도 받는다. 서드파티 외부 API는 호출 대상이 아니라 고객 공지 대상이다
    - CAUSE_UNKNOWN: 근거가 부족해 원인을 정하지 못했다 (RB-STD-07)
    - P1, NOT_RECOVERED, CHANGE_TIMEOUT: Agent 흐름이 정한다
    reason에는 필요한 조치와 할 수 없는 이유(계약 조항, 상한 값)를 한 문장으로 적는다.
    service를 비우면 인시던트의 서비스다. 결과 status QUEUED는 발송 대기열에 들어갔다는 뜻이다."""
    body = {"category": category, "reason": reason, "service": service, "requiredAction": required_action,
            "blockedBy": blocked_by, "dependency": dependency}
    return await _call("POST", f"/incidents/{incident_id}/escalate",
                       json={k: v for k, v in body.items() if v is not None})


@mcp.tool(annotations=WRITE)
async def execute_runbook_action(
    incident_id: int,
    action: Literal["restart_pods", "scale_deployment", "rollback_deployment"],
    service: str,
    replicas: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """고객사 서비스에 런북 조치를 실행한다.
    - restart_pods: 서비스의 파드를 재시작한다 (메모리에 묶인 상태가 풀린다)
    - scale_deployment: 레플리카 수를 replicas(1~10)로 바꾼다
    - rollback_deployment: 직전 배포(설정·이미지)로 되돌린다
    service는 get_service_inventory의 서비스 이름이다. reason에는 근거 런북 조항과 이유를 한 문장으로 적는다.
    반영 방식은 고객사 운영 방식에 따라 backend가 정한다. 결과 status가 EXECUTED면 바로 반영된 것이고,
    PR_CREATED면 설정 저장소 PR이 만들어진 것이다(병합·동기화 후 반영된다).
    계약상 허용되지 않은 조치는 거부되고, 오류 메시지에 허용 조치 목록이 담긴다."""
    # 순차 재시작은 파드마다 새 파드가 Ready가 되기를 기다려서 오래 걸린다 (파드당 최대 60초)
    return await _call("POST", "/actions", timeout=150, json={"incidentId": incident_id, "action": action,
                                                              "service": service, "replicas": replicas,
                                                              "reason": reason})


@mcp.tool(annotations=WRITE)
async def send_customer_notice(incident_id: int, message: str) -> dict[str, Any]:
    """고객사 담당자에게 장애 공지를 발송한다. message는 고객이 읽는 문장이므로 내부 용어와 추측을 넣지 않는다.
    MSP 관리자만 승인할 수 있다."""
    return await _call("POST", "/notices", json={"incidentId": incident_id, "message": message})


@mcp.tool(annotations=WRITE)
async def confirm_sla_credit(incident_id: int) -> dict[str, Any]:
    """인시던트의 SLA 크레딧을 확정한다. 다운타임(분)과 크레딧(%)은 backend가 계약 기준으로 계산한다.
    MSP 관리자만 승인할 수 있다."""
    return await _call("POST", "/sla-credits", json={"incidentId": incident_id})


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "9101")),
        middleware=[Middleware(ServiceTokenMiddleware, token=SERVICE_TOKEN)],
        show_banner=False,
    )

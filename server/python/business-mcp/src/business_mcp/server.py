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


async def _call(method: str, path: str, **kwargs: Any) -> Any:
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
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as client:
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
    """현재 고객사가 운영하는 서비스 이름과 각 서비스의 의존 대상(DB, 외부 API 등)을 돌려준다."""
    return await _call("GET", "/services")


# ── 변경 도구 (destructiveHint: agent Harness가 실행 전에 사람의 승인을 받는다) ──


@mcp.tool(annotations=WRITE)
async def create_incident(service: str, title: str, severity: Literal["P1", "P2", "P3"]) -> dict[str, Any]:
    """알림 없이 발견한 장애를 인시던트로 등록한다. 새 incidentId를 돌려준다."""
    return await _call("POST", "/incidents", json={"service": service, "title": title, "severity": severity})


@mcp.tool(annotations=WRITE)
async def escalate_incident(incident_id: int, reason: str) -> dict[str, Any]:
    """담당 엔지니어와 MSP 관리자를 호출한다. reason에는 에스컬레이션 이유를 한 문장으로 적는다."""
    return await _call("POST", f"/incidents/{incident_id}/escalate", json={"reason": reason})


@mcp.tool(annotations=WRITE)
async def execute_runbook_action(
    incident_id: int,
    action: Literal["adjust_pool", "restart", "clear_fault"],
    pool_size: int | None = None,
) -> dict[str, Any]:
    """고객사 서비스에 런북 조치를 실행한다.
    - adjust_pool: DB 커넥션 풀 크기를 pool_size로 바꾼다
    - restart: 서비스를 재시작한다
    - clear_fault: 주입된 장애를 해제한다
    계약상 허용되지 않은 조치는 거부되고, 오류 메시지에 허용 조치 목록이 담긴다."""
    params = {"size": pool_size} if pool_size is not None else {}
    return await _call("POST", "/actions", json={"incidentId": incident_id, "action": action, "params": params})


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

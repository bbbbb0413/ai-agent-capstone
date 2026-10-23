"""화면 요약 표에 쓰는 정형 요약.

에러 유형·원인·근거 조항은 모델이 고르고(prompts/summary.md), 조치·승인·반영(PR)·회복·공지는 State에서 그대로 옮긴다.
"""

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent import harness

log = logging.getLogger(__name__)

ErrorType = Literal["DB 커넥션 풀 고갈", "트래픽 급증", "파드 응답 불능", "외부 API 지연", "DB 자체 지연", "기타",
                    "원인 미확정"]


class Classification(BaseModel):
    error_type: ErrorType
    cause: str = Field(description="근본 원인 한 문장")
    other_causes: list[str] = Field(default_factory=list,
                                    description="그 밖의 원인 후보. 원인이 겹쳤을 때만 있다 (SPEC-compound-faults 4.4)")
    runbook_clause: str | None = Field(description="근거 런북·계약 조항 번호와 이름. 없으면 null")


async def classify(payload: str) -> dict[str, Any] | None:
    """분류에 실패해도 리포트는 내보내야 하므로 None을 돌려준다. 화면은 요약 표 없이 리포트만 보여준다."""
    messages = [SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("summary")), HumanMessage(payload)]
    try:
        result: Classification = await harness.structured_model(Classification).ainvoke(messages)
    except Exception:
        log.exception("summary classification failed")
        return None
    return result.model_dump()


def _escalation(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """화면에 보여 줄 만큼만: 분류, 받는 사람, 발송 상태. 메시지 본문은 backend 대기열에 있다."""
    if not result:
        return None
    return {key: result.get(key) for key in ("status", "escalationId", "category", "recipients", "error")
            if result.get(key) is not None}


def _decision(approvals: list[dict[str, Any]], tool: str) -> str | None:
    return next((a.get("decision") for a in reversed(approvals) if a.get("tool") == tool), None)


def build(state: dict[str, Any]) -> dict[str, Any] | None:
    classification = state.get("classification")
    if not classification:
        return None
    action = state.get("proposed_action") or {}
    change = state.get("change")
    approvals = state.get("approvals", [])
    incident = (state.get("context") or {}).get("incident") or {}
    return {
        **classification,
        "severity": incident.get("severity"),
        "service": incident.get("service"),
        "action": {"tool": action.get("tool"), "args": action.get("args"),
                   "decision": _decision(approvals, action["tool"]),
                   "result": state.get("action_result")} if action else None,
        "change": {key: change.get(key) for key in ("prNumber", "prUrl", "result")} if change else None,
        "recovered": state.get("recovered"),
        "escalation": _decision(approvals, "business_escalate_incident"),
        "escalation_result": _escalation(state.get("escalation_result")),
        "notice": _decision(approvals, "business_send_customer_notice"),
        "notice_result": state.get("notice_result") or (
            state.get("action_result") if action.get("tool") == "business_send_customer_notice" else None),
    }

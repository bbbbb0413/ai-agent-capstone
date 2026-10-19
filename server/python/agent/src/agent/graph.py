"""장애 대응 Graph (PDF A안: 직접 만든 StateGraph). 흐름은 기획안 6장의 그림과 같다.

LLM이 도구를 고르는 Node: diagnose, propose_action, verify, report
코드가 정해진 도구를 부르는 Node: load_context, retrieve_runbook, execute
사람의 결정을 기다리는 Node(interrupt): approval, escalate, notice_approval, await_change
  await_change는 GitOps 고객사의 PR이 병합되고 ArgoCD 동기화가 끝나기를 기다린다. backend가 동기화 알림을 받아 재개한다.

interrupt가 있는 Node는 재개될 때 처음부터 다시 실행된다. 그래서 interrupt 앞에는 부수효과(도구 호출, 모델 호출)를 두지 않는다.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent import harness, summary
from agent.backend import backend
from agent.toolbox import ToolBox, parse_json


class State(TypedDict, total=False):
    task_id: str
    tenant_id: str
    incident_id: int | None
    type: str                       # DIAGNOSE | QUESTION
    question: str
    trace_id: str
    context: dict[str, Any]         # 고객사·SLA·서비스 정보 (backend 계산값)
    plan: str
    messages: Annotated[list[AnyMessage], add_messages]
    findings: str
    evidence: list[dict[str, Any]]  # 답변의 근거 (도구, 요약)
    runbook: list[dict[str, Any]]   # 근거 문서 조항
    proposed_action: dict[str, Any] | None
    action_result: dict[str, Any] | None   # 조치 도구의 결과 (status: EXECUTED | PR_CREATED)
    change: dict[str, Any] | None          # GitOps PR과 반영 상태 (result: SYNCED | TIMEOUT)
    approvals: list[dict[str, Any]]
    turns: int
    tool_counts: dict[str, int]
    attempts: int                   # verify 실패 후 진단 재진입 횟수
    recovered: bool
    verification: str               # verify의 결론과 확인한 수치 (보고서 근거)
    report: str
    notice_draft: str
    classification: dict[str, Any] | None  # 에러 유형·원인·근거 조항 (summary.py)
    notice_result: dict[str, Any] | None


@dataclass
class Context:
    """Graph 실행마다 넘기는 값. checkpointer에 저장되지 않는다."""
    toolbox: ToolBox
    segment_started: float = field(default_factory=time.monotonic)
    elapsed_before: float = 0.0     # 이전 구간들의 실행 시간 (승인 대기 시간은 들어가지 않는다)
    waited: float = 0.0             # 이 구간에서 회복 확인을 위해 기다린 시간 (실행 시간에서 뺀다)

    def elapsed(self) -> float:
        return time.monotonic() - self.segment_started - self.waited

    def check_time(self) -> None:
        if self.elapsed_before + self.elapsed() > harness.TASK_SECONDS:
            raise harness.HarnessLimit(f"작업 시간 한도({harness.TASK_SECONDS}초)를 넘었다")

    async def settle(self, seconds: float) -> None:
        """Agent가 일하지 않고 시스템 반영을 기다리는 시간. 실행 시간 한도에 넣지 않는다."""
        started = time.monotonic()
        await asyncio.sleep(seconds)
        self.waited += time.monotonic() - started


Rt = Runtime[Context]


# ── 코드 Node ─────────────────────────────────────────────────


async def intake(state: State, runtime: Rt) -> dict:
    return {"turns": state.get("turns", 0), "tool_counts": state.get("tool_counts", {}),
            "attempts": state.get("attempts", 0), "evidence": state.get("evidence", []),
            "approvals": state.get("approvals", [])}


async def load_context(state: State, runtime: Rt) -> dict:
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    context: dict[str, Any] = {
        "tenant": parse_json(await box.call("load_context", "business_get_tenant_profile", {}, counts)),
        "services": parse_json(await box.call("load_context", "business_get_service_inventory", {}, counts)),
    }
    if state.get("incident_id"):
        incident_id = state["incident_id"]
        context["sla"] = parse_json(await box.call("load_context", "business_get_sla_terms",
                                                   {"incident_id": incident_id}, counts))
        incidents = parse_json(await box.call("load_context", "business_list_incidents", {"status": "OPEN"}, counts))
        if isinstance(incidents, list):
            context["incident"] = next((i for i in incidents if i.get("id") == incident_id), None)
    return {"context": context, "tool_counts": counts}


_DOCUMENT_QUESTION_MARKERS = (
    "sla", "계약", "약관", "정책", "런북", "runbook", "조항", "매뉴얼", "문서", "회고", "postmortem",
)


def _needs_document_lookup(state: State) -> bool:
    """문서성 질문만 RAG로 바로 보낸다. 모든 질문에 RAG를 강제하지는 않는다."""
    if state.get("type") != "QUESTION" or state.get("incident_id") is not None:
        return False
    question = state.get("question", "").lower()
    return any(marker in question for marker in _DOCUMENT_QUESTION_MARKERS)


def route_triage(state: State) -> Literal["escalate", "diagnose", "retrieve_runbook"]:
    if _needs_document_lookup(state):
        return "retrieve_runbook"
    incident = state.get("context", {}).get("incident") or {}
    return "escalate" if incident.get("severity") == "P1" and not state.get("approvals") else "diagnose"


async def retrieve_runbook(state: State, runtime: Rt) -> dict:
    counts = dict(state["tool_counts"])
    result = await runtime.context.toolbox.call(
        "retrieve_runbook", "rag_search_documents",
        {"query": state.get("findings") or state["question"], "top_k": 4}, counts)
    docs = parse_json(result)
    return {"runbook": docs if isinstance(docs, list) else [], "tool_counts": counts}


async def execute(state: State, runtime: Rt) -> dict:
    action, counts = state["proposed_action"], dict(state["tool_counts"])
    result = await runtime.context.toolbox.call("propose_action", action["tool"], action["args"], counts,
                                                incident_id=state.get("incident_id"))
    evidence = state["evidence"] + [{"tool": action["tool"], "status": result.status,
                                      "summary": str(result.content)[:500]}]
    parsed = parse_json(result)
    if result.status == "error":
        outcome = {"status": "FAILED", "error": str(parsed)[:500]}
    elif isinstance(parsed, dict) and parsed.get("status"):
        outcome = parsed
    elif isinstance(parsed, dict) and parsed.get("sent") is True:
        outcome = {"status": "SENT", **parsed}
    else:
        outcome = {"status": "UNCONFIRMED", "detail": str(parsed)[:500]}
    change = None
    if outcome and outcome["status"] == "PR_CREATED":
        change = {key: outcome.get(key) for key in ("action", "service", "changeMode", "prNumber", "prUrl", "files")}
    return {"evidence": evidence, "tool_counts": counts, "action_result": outcome, "change": change}


def route_execute(state: State) -> Literal["verify", "await_change", "report"]:
    # 고객 공지처럼 시스템을 바꾸지 않는 조치, 실행에 실패한 조치는 회복 확인 없이 보고서로 간다
    if state["proposed_action"]["tool"] != "business_execute_runbook_action":
        return "report"
    status = (state.get("action_result") or {}).get("status")
    if status == "PR_CREATED" and state.get("change"):
        return "await_change"
    return "verify" if status == "EXECUTED" else "report"


# ── LLM Node ──────────────────────────────────────────────────


async def _tool_loop(node: str, state: State, runtime: Rt, messages: list[AnyMessage],
                     max_turns: int) -> tuple[list[AnyMessage], int, dict[str, int], list[dict]]:
    """모델이 허용목록 안에서 도구를 고르고, Harness가 실행한다. 턴·시간·도구 한도를 지킨다."""
    box = runtime.context.toolbox
    model = harness.chat_model(box.for_node(node))
    turns, counts, evidence, new = state["turns"], dict(state["tool_counts"]), [], []
    for _ in range(max_turns):
        if turns >= harness.MAX_TURNS:
            break
        runtime.context.check_time()
        ai: AIMessage = await model.ainvoke(harness.compact(messages + new))
        turns += 1
        new.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            result = await box.call(node, call["name"], call["args"], counts, call["id"], state.get("incident_id"))
            new.append(result)
            evidence.append({"tool": call["name"], "args": call["args"], "status": result.status,
                             "summary": str(result.content)[:500]})
    return new, turns, counts, evidence


def _final_text(messages: list[AnyMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            return str(message.content)
    return ""


async def diagnose(state: State, runtime: Rt) -> dict:
    if state.get("messages"):
        intro = [HumanMessage("조치 후에도 회복되지 않았다. 조치 이후 시간대를 다시 조사한다.")]
    else:
        intro = [
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("diagnose")),
            HumanMessage(f"요청: {state['question']}\n\n고객사 컨텍스트(backend 계산값):\n"
                         f"{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}"),
        ]
    history = list(state.get("messages", [])) + intro
    new, turns, counts, evidence = await _tool_loop("diagnose", state, runtime, history, harness.MAX_TURNS)
    findings = _final_text(new) or "원인 미확정: 턴 한도 안에 근거를 모으지 못했다"
    update = {"messages": intro + new, "findings": findings, "turns": turns, "tool_counts": counts,
              "evidence": state["evidence"] + evidence}
    if not state.get("plan") and new and isinstance(new[0], AIMessage):
        update["plan"] = str(new[0].content)
    return update


def route_diagnose(state: State) -> Literal["retrieve_runbook", "report"]:
    return "report" if state["findings"].startswith("원인 미확정") else "retrieve_runbook"


async def propose_action(state: State, runtime: Rt) -> dict:
    # 변경 도구는 incident_id가 필수다. 연결되지 않은 질문은 근거와 권고를 보고서로
    # 반환하고, 유효한 인시던트가 연결된 작업에서만 실행안을 제안한다.
    if state.get("incident_id") is None:
        return {"proposed_action": None}
    runtime.context.check_time()
    if state["turns"] >= harness.MAX_TURNS:
        return {"proposed_action": None}
    model = harness.chat_model(runtime.context.toolbox.for_node("propose_action"), harness.PROPOSE_REASONING_EFFORT,
                               tool_choice="required")
    messages = [
        SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("propose")),
        HumanMessage(f"진단 결과:\n{state['findings']}\n\n런북·계약 조항:\n"
                     f"{json.dumps(state.get('runbook', []), ensure_ascii=False)}\n\n"
                     f"고객사 컨텍스트:\n{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}"),
    ]
    ai: AIMessage = await model.ainvoke(messages)
    if not ai.tool_calls:
        return {"proposed_action": None, "turns": state["turns"] + 1}
    call = ai.tool_calls[0]
    if call["name"] == "business_execute_runbook_action" and not {"action", "service"} <= call["args"].keys():
        # 필수 인자가 빠진 조치는 승인 요청으로 올리지 않는다. 사람이 빈 조치를 승인하게 만들지 않는다
        return {"proposed_action": None, "turns": state["turns"] + 1}
    args = {**call["args"], "incident_id": state.get("incident_id")} if "incident_id" in call["args"] else call["args"]
    return {"proposed_action": {"tool": call["name"], "args": args, "reason": str(ai.content)},
            "turns": state["turns"] + 1}


def route_propose(state: State) -> Literal["approval", "report"]:
    return "approval" if state.get("proposed_action") else "report"


# 회복 확인의 표준 메트릭. 알림 규칙(infra/prometheus/alert-rules.yml)과 같은 기준을 조치 직후 15초 창으로 본다.
# 에러가 하나도 없으면 에러 시계열이 없으므로 `or vector(0)`을 붙인다. 요청 자체가 없으면 결과가 비어 None이 된다.
VERIFY_QUERIES = {
    "error_rate": '(sum(rate(demo_orders_seconds_count{outcome="error"}[15s])) or vector(0))'
                  ' / sum(rate(demo_orders_seconds_count[15s]))',
    "p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[15s])))',
}
ERROR_RATE_THRESHOLD = 0.2
P95_THRESHOLD_SECONDS = 1.0


async def _metric(box: ToolBox, expr: str, counts: dict[str, int]) -> float | None:
    result = parse_json(await box.call("verify", "grafana_query_prometheus",
                                       {"expr": expr, "queryType": "instant", "endTime": "now"}, counts))
    try:
        return float(result["data"][0]["value"][1])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


async def verify(state: State, runtime: Rt) -> dict:
    """조치 후 메트릭을 다시 보고 회복 여부를 판단한다 (Reflection).

    쿼리는 코드가 정해진 식으로 조회하고, 모델은 그 수치로 판단만 한다. 모델에게 PromQL을 맡겼을 때
    메트릭 이름을 지어내거나(reservation_api_requests_…) 조회 없이 결론을 낸 적이 있다 (docs/harness-spec.md).
    """
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    await backend.emit(state["task_id"], "STEP", {"node": "verify", "waitSeconds": harness.VERIFY_SETTLE_SECONDS})
    await runtime.context.settle(harness.VERIFY_SETTLE_SECONDS)  # 조치가 메트릭에 반영될 시간
    error_rate = await _metric(box, VERIFY_QUERIES["error_rate"], counts)
    if error_rate is None:  # 재시작한 파드의 메트릭이 아직 수집되지 않았다. 한 번만 더 기다린다
        await runtime.context.settle(harness.VERIFY_RETRY_SECONDS)
        error_rate = await _metric(box, VERIFY_QUERIES["error_rate"], counts)
    metrics = {"error_rate": error_rate, "p95_seconds": await _metric(box, VERIFY_QUERIES["p95_seconds"], counts),
               "window": "조치 직후 15초", "thresholds": {"error_rate": ERROR_RATE_THRESHOLD,
                                                        "p95_seconds": P95_THRESHOLD_SECONDS}}
    evidence = state["evidence"] + [{"tool": "grafana_query_prometheus", "args": VERIFY_QUERIES,
                                     "summary": json.dumps(metrics, ensure_ascii=False)}]

    turns = state["turns"]
    if turns < harness.MAX_TURNS:
        runtime.context.check_time()
        ai: AIMessage = await harness.chat_model().ainvoke([
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("verify")),
            HumanMessage(f"실행한 조치: {json.dumps(state['proposed_action'], ensure_ascii=False)}\n"
                         f"진단 결과: {state['findings']}\n"
                         f"조치 후 메트릭: {json.dumps(metrics, ensure_ascii=False)}"),
        ])
        turns += 1
        verdict = str(ai.content)
        recovered = verdict.strip().upper().startswith("RECOVERED")
    else:  # 턴 한도를 다 썼으면 알림 기준으로 코드가 판단한다
        recovered = (error_rate is not None and error_rate < ERROR_RATE_THRESHOLD
                     and (metrics["p95_seconds"] or 0) < P95_THRESHOLD_SECONDS)
        verdict = f"{'RECOVERED' if recovered else 'NOT_RECOVERED'} (턴 한도로 기준값 판단)\n{json.dumps(metrics)}"
    return {"recovered": recovered, "verification": verdict, "turns": turns, "tool_counts": counts,
            "evidence": evidence}


def route_verify(state: State) -> Literal["report", "diagnose", "final_escalate"]:
    if state["recovered"]:
        return "report"
    return "diagnose" if state["attempts"] < 1 else "final_escalate"


async def bump_attempt(state: State) -> dict:
    return {"attempts": state["attempts"] + 1}


async def report(state: State, runtime: Rt) -> dict:
    """보고서를 TOKEN 이벤트로 흘려보내며 작성한다. 마지막의 '고객 공지 문안' 절은 notice_approval에서 쓴다.

    화면 요약 표에 쓸 분류는 보고서와 같은 입력으로 동시에 만든다.
    """
    payload = json.dumps({
        "type": state.get("type"), "incident_id": state.get("incident_id"),
        "request": state["question"], "plan": state.get("plan"), "findings": state.get("findings"),
        "evidence": state.get("evidence", []), "runbook": state.get("runbook", []),
        "action": state.get("proposed_action"), "approvals": state.get("approvals", []),
        "recovered": state.get("recovered"), "verification": state.get("verification"),
        "action_result": state.get("action_result"), "change": state.get("change"),
        "context": state.get("context", {}),
    }, ensure_ascii=False, default=str)

    information_question = state.get("type") == "QUESTION" and state.get("incident_id") is None

    async def write() -> str:
        report_prompt = "answer" if information_question else "report"
        messages = [SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt(report_prompt)),
                    HumanMessage(payload)]
        chunks: list[str] = []
        async for chunk in harness.chat_model().astream(messages):
            if chunk.content:
                chunks.append(str(chunk.content))
                await backend.emit(state["task_id"], "TOKEN", {"text": chunk.content})
        return "".join(chunks)

    await backend.emit(state["task_id"], "STEP", {"node": "report"})
    # 인시던트와 무관한 일반 질문에는 장애 원인 분류표가 의미가 없고, 오히려 없는 원인을 만들 수 있다.
    if information_question:
        text, classification = await write(), None
    else:
        text, classification = await asyncio.gather(write(), summary.classify(payload))
    body, _, notice = text.partition("## 고객 공지 문안")
    return {"report": body.strip(), "notice_draft": notice.strip(), "classification": classification}


def route_report(state: State) -> Literal["notice_approval", "__end__"]:
    sent = (state.get("proposed_action") or {}).get("tool") == "business_send_customer_notice"
    return "notice_approval" if state.get("incident_id") and state.get("notice_draft") and not sent else END


# ── 사람의 결정을 기다리는 Node (interrupt) ───────────────────────


def _approval_request(tool: str, args: dict, reason: str, state: State) -> dict:
    request = {"tool": tool, "args": args, "reason": reason,
               "approvalId": str(uuid5(NAMESPACE_URL, f"{state['task_id']}:{tool}:{len(state.get('approvals', []))}")),
               "evidence": state.get("evidence", [])[-5:], "runbook": state.get("runbook", [])[:3]}
    if tool == "business_execute_runbook_action":
        # 화면의 승인 카드에 반영 방식을 보여 준다. 방식은 backend가 정한 고객사 값이다
        request["changeMode"] = (state.get("context", {}).get("tenant") or {}).get("changeMode")
    return request


async def approval(state: State) -> dict:
    action = state["proposed_action"]
    decision = interrupt(_approval_request(action["tool"], action["args"], action["reason"], state))
    return {"approvals": state["approvals"] + [{**decision, "tool": action["tool"]}]}


def route_approval(state: State) -> Literal["execute", "report"]:
    return "execute" if state["approvals"][-1].get("decision") == "APPROVE" else "report"


async def await_change(state: State) -> dict:
    """PR 병합과 동기화를 기다린다. 이 대기 시간은 실행 시간 한도에 들어가지 않는다 (runner가 구간별로 잰다)."""
    change = state["change"]
    decision = interrupt({"kind": "CHANGE", "tool": state["proposed_action"]["tool"],
                          "args": state["proposed_action"]["args"], **change})
    return {"change": {**change, "result": decision.get("decision"), "comment": decision.get("comment")}}


def route_await_change(state: State) -> Literal["verify", "final_escalate"]:
    return "verify" if state["change"].get("result") == "SYNCED" else "final_escalate"


def _escalation_reason(state: State) -> str:
    if (state.get("change") or {}).get("result") == "TIMEOUT":
        return f"조치 PR이 기한 안에 병합되지 않음 ({state['change'].get('prUrl')})"
    return "P1 장애 발생" if not state.get("attempts") else "조치 후에도 회복되지 않음"


async def escalate(state: State, runtime: Rt) -> dict:
    """P1이거나 회복에 실패하거나 PR이 병합되지 않으면 담당자 호출을 제안한다. 승인되면 호출한다."""
    reason = _escalation_reason(state)
    args = {"incident_id": state["incident_id"], "reason": reason}
    decision = interrupt(_approval_request("business_escalate_incident", args, reason, state))
    approvals = state["approvals"] + [{**decision, "tool": "business_escalate_incident"}]
    counts = dict(state["tool_counts"])
    if decision.get("decision") == "APPROVE":
        await runtime.context.toolbox.call("escalate", "business_escalate_incident", args, counts,
                                           incident_id=state["incident_id"])
    return {"approvals": approvals, "tool_counts": counts}


async def notice_approval(state: State, runtime: Rt) -> dict:
    args = {"incident_id": state["incident_id"], "message": state["notice_draft"]}
    decision = interrupt(_approval_request("business_send_customer_notice", args, "고객 공지 발송", state))
    counts = dict(state["tool_counts"])
    if decision.get("decision") == "APPROVE":
        result = await runtime.context.toolbox.call("notice", "business_send_customer_notice", args, counts,
                                                    incident_id=state["incident_id"])
        outcome = parse_json(result)
        notice_result = ({"status": "SENT"} if result.status != "error" and isinstance(outcome, dict)
                         and outcome.get("sent") is True else {"status": "FAILED", "error": str(outcome)[:500]})
    else:
        notice_result = {"status": "REJECTED"}
    return {"approvals": state["approvals"] + [{**decision, "tool": "business_send_customer_notice"}],
            "tool_counts": counts, "notice_result": notice_result}


# ── Graph ─────────────────────────────────────────────────────


def build(checkpointer):
    g = StateGraph(State, context_schema=Context)
    for name, node in [
        ("intake", intake), ("load_context", load_context), ("escalate", escalate), ("diagnose", diagnose),
        ("retrieve_runbook", retrieve_runbook), ("propose_action", propose_action), ("approval", approval),
        ("execute", execute), ("await_change", await_change), ("verify", verify), ("bump_attempt", bump_attempt),
        ("final_escalate", escalate), ("report", report), ("notice_approval", notice_approval),
    ]:
        g.add_node(name, node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "load_context")
    g.add_conditional_edges("load_context", route_triage)          # triage
    g.add_edge("escalate", "diagnose")
    g.add_conditional_edges("diagnose", route_diagnose)
    g.add_edge("retrieve_runbook", "propose_action")
    g.add_conditional_edges("propose_action", route_propose)
    g.add_conditional_edges("approval", route_approval)
    g.add_conditional_edges("execute", route_execute)
    g.add_conditional_edges("await_change", route_await_change)
    g.add_conditional_edges("verify", route_verify, {"report": "report", "diagnose": "bump_attempt",
                                                     "final_escalate": "final_escalate"})
    g.add_edge("bump_attempt", "diagnose")
    g.add_edge("final_escalate", "report")
    g.add_conditional_edges("report", route_report)
    g.add_edge("notice_approval", END)
    return g.compile(checkpointer=checkpointer)

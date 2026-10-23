"""장애 대응 Graph (PDF A안: 직접 만든 StateGraph). 흐름은 기획안 6장의 그림과 같다.

LLM이 도구를 고르는 Node: diagnose, propose_action, verify, report
코드가 정해진 도구를 부르는 Node: load_context, retrieve_runbook, execute
코드가 바로 답하는 Node: no_issue (인식된 장애가 없는 질문)
사람의 결정을 기다리는 Node(interrupt): approval, escalate, notice_approval, await_change
  await_change는 GitOps 고객사의 PR이 병합되고 ArgoCD 동기화가 끝나기를 기다린다. backend가 동기화 알림을 받아 재개한다.

interrupt가 있는 Node는 재개될 때 처음부터 다시 실행된다. 그래서 interrupt 앞에는 부수효과(도구 호출, 모델 호출)를 두지 않는다.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent import action_guards, attachments, customer_notice, harness, jev, signals, summary
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
    past_incidents: list[dict[str, Any]]  # 자동 수집한 과거 보고서. 현재 원인의 확정 근거는 아니다
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
    escalation_result: dict[str, Any] | None   # 에스컬레이션 발송 대기열 등록 결과 (받는 사람, 메시지, status QUEUED)
    no_action_reason: str | None    # 조치하지 않기로 한 이유 (HPA가 확장 중일 때만 생긴다)
    signals: dict[str, Any]         # 진단 때 코드가 조회한 지표, 로그 신호 건수, 의존 대상 상태, 원인 후보별 사실 표(facts)
    causes: list[str]               # 진단이 적은 원인 후보 조항 (순위 순서, 모델이 정한다. SPEC-compound-faults)
    target_clause: str | None       # 진단이 적은 처리할 원인. 조치 검사의 기준이다
    handled: list[dict[str, Any]]   # 이 작업에서 앱 조치를 한 원인 {clause, action, signal, signal_count_before, at, ...}


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
            "approvals": state.get("approvals", []), "handled": state.get("handled", [])}


async def load_context(state: State, runtime: Rt) -> dict:
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    async def read(name, args):
        try:
            return await box.call("load_context", name, args, counts)
        except Exception:
            if state.get("type") != "ATTACHMENT":
                raise
            return ToolMessage(content="고객사 컨텍스트 조회 실패 — 확인 불가", status="error", tool_call_id="context")
    context: dict[str, Any] = {
        "tenant": parse_json(await read("business_get_tenant_profile", {})),
        "services": parse_json(await read("business_get_service_inventory", {})),
    }
    if state.get("incident_id"):
        incident_id = state["incident_id"]
        context["sla"] = parse_json(await read("business_get_sla_terms", {"incident_id": incident_id}))
        incidents = parse_json(await read("business_list_incidents", {"status": "OPEN"}))
        if isinstance(incidents, list):
            context["incident"] = next((i for i in incidents if i.get("id") == incident_id), None)
    else:
        # 질문 작업: 인식된 장애가 있는지 본다. 조회에 실패하면(None) 장애가 없다고 단정하지 않는다
        incidents = parse_json(await read("business_list_incidents", {"status": "OPEN"}))
        if incidents == "":
            incidents = []  # MCP 어댑터는 빈 목록을 빈 내용으로 넘긴다
        context["open_incidents"] = incidents if isinstance(incidents, list) else None
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


_SYMPTOM_MARKERS = (
    "느려", "지연", "오류", "에러", "실패", "장애", "안 돼", "안되", "먹통", "timeout", "error", "slow",
)


def _reported_service(state: State) -> str | None:
    """알림 없이 들어온 제보는 질문에 서비스명이 분명할 때만 등록 후보로 삼는다.

    모호한 "서비스가 느리다"를 임의의 서비스 인시던트로 만들면 잘못된 담당자에게 알림이 갈 수 있다.
    서비스 선택 UI가 생기기 전까지는 질문 속 서비스명과 inventory 이름이 정확히 맞는 경우만 허용한다.
    """
    question = state.get("question", "").lower()
    for service in (state.get("context", {}).get("services") or []):
        name = service.get("name") if isinstance(service, dict) else None
        if isinstance(name, str) and name.lower() in question:
            return name
    return None


def _needs_incident_registration(state: State) -> bool:
    if state.get("type") != "QUESTION" or state.get("incident_id") is not None:
        return False
    if state.get("context", {}).get("open_incidents") != []:
        return False
    question = state.get("question", "").lower()
    return _reported_service(state) is not None and any(marker in question for marker in _SYMPTOM_MARKERS)


def route_triage(state: State) -> Literal["escalate", "diagnose", "retrieve_runbook", "register_incident", "no_issue", "analyze_attachments"]:
    if state.get("type") == "ATTACHMENT":
        return "analyze_attachments"
    if _needs_document_lookup(state):
        return "retrieve_runbook"
    context = state.get("context", {})
    if not state.get("incident_id") and context.get("open_incidents") == []:
        if _needs_incident_registration(state):
            return "register_incident"
        return "no_issue"
    incident = context.get("incident") or {}
    return "escalate" if incident.get("severity") == "P1" and not state.get("approvals") else "diagnose"


async def no_issue(state: State) -> dict:
    """인식된 장애(열린 인시던트)가 없는 질문에는 진단·조치 없이 이상 없음으로 답한다 (docs/기능별-변경사항.md 7장)."""
    tenant = (state.get("context", {}).get("tenant") or {}).get("name") or state["tenant_id"]
    if state.get("registration_rejected"):
        text = (f"## 인시던트 미등록\n\n현재 {tenant}에 처리 중인 인시던트가 없고, 제보 기반 인시던트 등록도 승인되지 않았다.\n\n"
                "문제가 계속되면 서비스명과 증상을 함께 적어 다시 등록을 요청하거나 담당 엔지니어에게 확인을 요청한다.")
    else:
        text = (f"## 이상 없음\n\n현재 {tenant}에 인식된 장애(처리 중인 인시던트)가 없다.\n\n"
                "알림 기준에 못 미치는 문제는 여기에 나타나지 않는다. 문제가 계속되면 담당 엔지니어에게 확인을 요청한다.")
    await backend.emit(state["task_id"], "TOKEN", {"text": text})
    return {"report": text}


async def retrieve_runbook(state: State, runtime: Rt) -> dict:
    """MSP 표준 런북(장애 형태별 조치)과 이 고객사 문서(서비스 런북·계약)를 따로 찾아 합친다.

    한 번에 찾으면 표준 런북이 상위를 채워 서비스별 주의 사항과 계약 조항이 밀려난다 (SPEC-fault-based-actions 1단계).
    계약의 허용 조치 조항은 증상과 문장이 달라 증상으로는 검색되지 않는다. 그래서 정해진 질문으로 따로 찾는다.
    이 Node는 정해진 검색만 하므로, 재진단 뒤 다시 와도 검색할 수 있게 같은 도구 한도를 방문마다 새로 센다.
    """
    counts = {k: v for k, v in state["tool_counts"].items() if not k.startswith("retrieve_runbook:")}
    query = state.get("findings") or state["question"]
    searches = [
        {"query": query, "top_k": 2, "scope": "common"},
        {"query": query, "top_k": 2, "scope": "tenant"},
        {"query": "MSP가 사전 동의 없이 수행할 수 있는 허용 조치와 고객사 승인이 필요한 조치", "top_k": 1,
         "scope": "tenant", "doc_type": "sla"},
    ]
    if state.get("incident_id") is not None:
        # 회고가 쌓여도 조치의 기준인 런북을 검색 결과에서 밀어내지 않는다.
        searches[0]["doc_type"] = "runbook"
        searches[1]["doc_type"] = "runbook"
    docs: list[dict[str, Any]] = []
    for args in searches:
        try:
            found = parse_json(await runtime.context.toolbox.call("retrieve_runbook", "rag_search_documents", args, counts))
        except Exception:
            if state.get("type") != "ATTACHMENT":
                raise
            found = None
        docs += found if isinstance(found, list) else []
    unique = list({(d.get("uri"), d.get("section")): d for d in docs}.values())  # 검색이 겹치면 한 번만
    return {"runbook": unique, "tool_counts": {**state["tool_counts"], **counts}}


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
    update: dict[str, Any] = {}
    if action["tool"] == "business_escalate_incident":   # 조치 대신 고른 에스컬레이션 (RB-STD-05~07)
        update["escalation_result"] = outcome
    if action["tool"] == "business_execute_runbook_action" and outcome["status"] in ("EXECUTED", "PR_CREATED"):
        update["handled"] = state.get("handled", []) + [_handled(state, action)]
    change = None
    if outcome and outcome["status"] == "PR_CREATED":
        change = {key: outcome.get(key) for key in ("action", "service", "changeMode", "prNumber", "prUrl", "files")}
    return {"evidence": evidence, "tool_counts": counts, "action_result": outcome, "change": change, **update}


def _handled(state: State, action: dict) -> dict[str, Any]:
    """앱 조치를 한 원인. 회복되지 않으면 verify가 그 신호를 조치 뒤 구간으로 다시 센다 (signal_count_after)."""
    clause = state.get("target_clause")
    signal = next((name for name, c in signals.LOG_SIGNALS.items() if c == clause), None)
    return {"clause": clause, "action": (action.get("args") or {}).get("action"), "signal": signal,
            "signal_count_before": ((state.get("signals") or {}).get("logs") or {}).get(signal),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def route_execute(state: State) -> Literal["verify", "await_change", "report"]:
    # 고객 공지처럼 시스템을 바꾸지 않는 조치, 실행에 실패한 조치는 회복 확인 없이 보고서로 간다
    if state["proposed_action"]["tool"] != "business_execute_runbook_action":
        return "report"
    status = (state.get("action_result") or {}).get("status")
    if status == "PR_CREATED" and state.get("change"):
        return "await_change"
    return "verify" if status == "EXECUTED" else "report"


# ── LLM Node ──────────────────────────────────────────────────


async def _tool_loop(node: str, state: State, runtime: Rt, messages: list[AnyMessage], max_turns: int,
                     force_first_tool: bool = False) -> tuple[list[AnyMessage], int, dict[str, int], list[dict]]:
    """모델이 허용목록 안에서 도구를 고르고, Harness가 실행한다. 턴·시간·도구 한도를 지킨다.

    force_first_tool이면 첫 턴은 도구를 반드시 하나 이상 부른다. 어떤 도구를 부를지는 모델이 고른다.
    """
    box = runtime.context.toolbox
    model = harness.chat_model(box.for_node(node))
    first = harness.chat_model(box.for_node(node), tool_choice="required") if force_first_tool else model
    turns, counts, evidence, new = state["turns"], dict(state["tool_counts"]), [], []
    for i in range(max_turns):
        if turns >= harness.MAX_TURNS:
            break
        runtime.context.check_time()
        ai: AIMessage = await (first if i == 0 else model).ainvoke(harness.compact(messages + new))
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


async def retrieve_history(state: State, box: ToolBox, counts: dict[str, int], log_signals: dict) -> list[dict]:
    """첫 진단에 과거 사례를 공급한다. 검색 실패·회고 없음은 현재 지표 진단을 막지 않는다."""
    tenant = state.get("tenant_id")
    if not tenant:
        return []
    incident = state.get("context", {}).get("incident") or {}
    args = {"query": f"{incident.get('service') or ''} {state.get('question', '')} "
                     f"로그 신호: {json.dumps(log_signals, ensure_ascii=False)}",
            "doc_type": "postmortem", "scope": "tenant", "top_k": 2,
            "exclude_doc_id": f"postmortem-incident-{state['incident_id']}" if state.get("incident_id") else None}
    try:
        response = await asyncio.wait_for(box.call("retrieve_history", "rag_search_documents", args, counts,
                                                   max_chars=18000), timeout=8)
        if response.status == "error":
            return []
        found = parse_json(response)
    except Exception:
        # 타임아웃·MCP 전송 예외는 보조 검색 실패다. 주 진단의 재시도 예산을 소비하지 않는다.
        return []
    if not isinstance(found, list):
        return []
    return [{**d, "content": str(d.get("content", ""))[:1800]} for d in found
            if isinstance(d, dict) and d.get("doc_type") == "postmortem"
            and str(d.get("uri", "")).startswith(f"docs://{tenant}/")
            and d.get("doc_id") != args["exclude_doc_id"]][:2]


def _diagnosed_service(state: State) -> str | None:
    """진단할 서비스: 인시던트의 서비스. 인시던트 없는 질문이면 질문 속 서비스나, 열린 인시던트가 한 서비스뿐일 때 그 서비스."""
    context = state.get("context", {})
    service = (context.get("incident") or {}).get("service") or _reported_service(state)
    if service:
        return service
    opened = {i.get("service") for i in (context.get("open_incidents") or []) if isinstance(i, dict)}
    return opened.pop() if len(opened) == 1 else None


async def diagnose(state: State, runtime: Rt) -> dict:
    prefetch: list[dict] = []
    past_incidents = state.get("past_incidents", [])
    service = _diagnosed_service(state)
    obs = signals.observability(state.get("context", {}), service)
    if obs is None:
        # 다른 서비스의 지표로 진단하지 않는다. 데모 앱 기본값으로 대신하지 않고 사유를 남긴다 (SPEC-limitations-fixes 4단계)
        findings = (f"원인 미확정: {service}의 관측 정의(서비스 카탈로그)가 없어 지표와 로그를 조회할 수 없다"
                    if service else "원인 미확정: 진단할 서비스를 정할 수 없다 (인시던트나 질문에 서비스가 없다)")
        return {"findings": findings, "evidence": state["evidence"] + [{"tool": "service_catalog", "status": "error",
                                                                         "summary": findings}]}
    if state.get("messages"):
        # 조치 후 재진단. 로그 신호와 지표를 지금 시점으로 다시 센다. 첫 진단 때는 경고 로그가 아직 Loki에 들어오지
        # 않아 0건이었고, 그 값을 그대로 쓰자 장애 형태 표에 맞는 행이 없어 원인 미확정이 된 적이 있다
        # (A사 a-surge-max, docs/harness-spec.md). 정해진 조회라 모델의 탐색 한도에 넣지 않는다
        logs, logged, metrics = await signals.observe(runtime.context.toolbox, {}, state.get("incident_id"),
                                                      state.get("context", {}).get("incident"), obs, service)
        # 원인 후보별 사실은 마지막 조치 이후 구간으로 다시 센다. 조치 전의 신호가 남아 처리한 원인이 그대로인 것처럼 보이지 않게
        since = _last_action_at(state) or signals.log_window_start(datetime.now(timezone.utc),
                                                                    state.get("context", {}).get("incident"))
        observed = await _cause_inputs(runtime.context.toolbox, state, obs, service, since, logged, metrics)
        state = {**state, "signals": {**(state.get("signals") or {}), **observed}}
        prefetch = [logged, {"tool": "grafana_query_prometheus", "args": signals.diagnose_queries(obs, service),
                             "summary": json.dumps(metrics, ensure_ascii=False)},
                    {"tool": "cause_facts", "summary": json.dumps(observed["facts"], ensure_ascii=False)[:500]}]
        intro = [HumanMessage(f"조치 후에도 회복되지 않았다. 조치 이후 시간대를 다시 조사한다.\n\n"
                              f"이미 조치한 원인(코드가 기록, signal_count_after는 조치 뒤 다시 센 건수):\n"
                              f"{json.dumps(state.get('handled', []), ensure_ascii=False)}\n\n"
                              f"지금 시점의 ERROR·WARN 로그 반복 메시지(코드가 조회, 많은 순):\n{harness.as_untrusted(logs)}\n\n"
                              f"현재 지표(코드가 조회, 최근 1분):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                              f"원인 후보별 사실(코드가 조회, 마지막 조치 이후. 순위는 정하지 않았다):\n"
                              f"{json.dumps(observed['facts'], ensure_ascii=False)}\n\n"
                              f"의존 대상 상태(코드가 조회):\n{json.dumps(observed['dependencies'], ensure_ascii=False)}\n\n"
                              "진단 단계의 출력 형식으로 원인 후보와 처리할 원인을 다시 적는다. 이미 조치한 원인은 처리할 원인으로 "
                              "고르지 않는다. 그 원인의 신호가 남아 있으면 같은 원인에 앱 조치를 다시 하지 말고 에스컬레이션한다.")]
    else:
        counts = dict(state["tool_counts"])
        logs, logged, metrics = await signals.observe(runtime.context.toolbox, counts, state.get("incident_id"),
                                                      state.get("context", {}).get("incident"), obs, service)
        # 직전 변경 여부는 RB-STD-01(롤백)과 RB-STD-02(재시작)를 가른다. 모델이 조회하지 않고 "직전 변경 없음"이라고
        # 단정해 롤백 대신 재시작을 제안한 적이 있어 코드가 미리 조회한다 (docs/harness-spec.md)
        changes = parse_json(await runtime.context.toolbox.call(
            "diagnose", "business_list_recent_changes", {"service": service}, {}))
        past_incidents = await retrieve_history(state, runtime.context.toolbox, counts, logged.get("signals", {}))
        started_at = (state.get("context", {}).get("incident") or {}).get("startedAt")
        since = signals.log_window_start(datetime.now(timezone.utc), state.get("context", {}).get("incident"))
        observed = await _cause_inputs(runtime.context.toolbox, state, obs, service, since, logged, metrics)
        state = {**state, "tool_counts": counts, "signals": {**observed,
                                                             "recent_change": signals.recent_change(changes, started_at)}}
        prefetch = [logged, {"tool": "grafana_query_prometheus", "args": signals.diagnose_queries(obs, service),
                             "summary": json.dumps(metrics, ensure_ascii=False)},
                    {"tool": "business_list_recent_changes", "args": {"service": service},
                     "summary": json.dumps(changes, ensure_ascii=False, default=str)[:500]},
                    {"tool": "cause_facts", "summary": json.dumps(observed["facts"], ensure_ascii=False)[:500]}]
        intro = [
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("diagnose")),
            HumanMessage(f"요청: {state['question']}\n\n진단할 서비스: {service}. 아래 지표·로그는 이 서비스만 조회했다. "
                         f"직접 조회할 때도 service=\"{service}\" 조건을 붙인다. "
                         f"관측 정의(서비스 카탈로그): {json.dumps(obs, ensure_ascii=False)}\n\n"
                         f"고객사 컨텍스트(backend 계산값):\n"
                         f"{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}\n\n"
                         f"{logged['args']['startRfc3339']} 이후 ERROR·WARN 로그의 반복 메시지"
                         f"(코드가 조회, 많은 순. 인시던트 시작 2분 전부터, 최대 10분):\n"
                         f"{harness.as_untrusted(logs)}\n\n"
                         f"현재 지표(코드가 조회, 최근 1분. 에러율은 0~1):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                         f"원인 후보별 사실(코드가 조회, 인시던트 시작 2분 전부터. 순위는 정하지 않았다. "
                         f"signal_count는 Loki가 센 건수이고 null이면 셀 수 없다):\n"
                         f"{json.dumps(state['signals']['facts'], ensure_ascii=False)}\n\n"
                         f"의존 대상 상태(코드가 조회, 최근 1분):\n"
                         f"{json.dumps(state['signals']['dependencies'], ensure_ascii=False)}\n\n"
                         f"인시던트 시작: {(state.get('context', {}).get('incident') or {}).get('startedAt')}\n"
                         f"최근 배포·설정 변경(코드가 조회, 최신순):\n"
                         f"{harness.as_untrusted(json.dumps(changes, ensure_ascii=False, default=str)[:3000])}\n\n"
                         f"과거 유사 사례(미검토 보고서, 현재 근거와 비교할 참고 자료):\n"
                         f"{harness.as_untrusted(json.dumps(past_incidents, ensure_ascii=False))}"),
        ]
    history = list(state.get("messages", [])) + intro
    new, turns, counts, evidence = await _tool_loop("diagnose", state, runtime, history, harness.MAX_TURNS,
                                                    force_first_tool=harness.DIAGNOSE_FORCE_FIRST_TOOL)
    findings = _final_text(new) or "원인 미확정: 턴 한도 안에 근거를 모으지 못했다"
    if not any(isinstance(m, ToolMessage) and m.status != "error" for m in new):
        # 근거 없는 글(조사 계획 등)이 런북 검색어와 조치 제안의 입력이 되지 않게 한다
        findings = "원인 미확정: 도구로 근거를 모으지 못했다"
    causes, target = parse_causes(findings)
    update = {"messages": intro + new, "findings": findings, "turns": turns, "tool_counts": counts,
              "evidence": state["evidence"] + prefetch + evidence, "past_incidents": past_incidents,
              "causes": causes, "target_clause": target}
    if state.get("signals"):
        update["signals"] = state["signals"]
    if not state.get("plan") and new and isinstance(new[0], AIMessage):
        update["plan"] = str(new[0].content)
    return update


async def _cause_inputs(box: ToolBox, state: State, obs: dict, service: str, since: datetime, logged: dict,
                       metrics: dict) -> dict[str, Any]:
    """원인 후보를 판단할 사실: 신호별 건수(Loki가 셈), 의존 대상 상태, 원인 후보별 사실 표 (SPEC-compound-faults 3장).

    로그 신호 건수(logs)는 Loki가 센 값으로 바꾼다. 200줄 요약으로 센 값은 함께 난 다른 신호를 0건으로 셀 수 있다.
    Loki 집계에 실패한 신호는 요약으로 센 값을 남긴다.
    """
    details = await signals.log_signal_details(box, obs, service, since, datetime.now(timezone.utc))
    dependencies = await signals.dependency_health(box, state.get("context", {}), service)
    logs = {**logged.get("signals", {}), **{name: d["count"] for name, d in details.items()}}
    return {"metrics": metrics, "logs": logs, "log_details": details, "dependencies": dependencies,
            "facts": signals.cause_facts(details, dependencies, metrics)}


_CAUSE_LINE = re.compile(r"^\s*\d+[).]\s*(RB-STD-0\d)", re.MULTILINE)
_TARGET_LINE = re.compile(r"^\s*처리할 원인\s*[:：]\s*(RB-STD-0\d)", re.MULTILINE)


def parse_causes(findings: str) -> tuple[list[str], str | None]:
    """진단 출력의 `원인 후보:` 목록(조항, 적힌 순서)과 `처리할 원인:` 조항. 형식이 깨졌으면 ([], None)이고
    조치 검사는 처리할 원인 없이 지금처럼 동작한다."""
    if findings.startswith("원인 미확정"):
        return [], None
    head, _, rest = findings.partition("원인 후보")
    causes = list(dict.fromkeys(_CAUSE_LINE.findall(rest.split("처리할 원인")[0]))) if rest else []
    target = _TARGET_LINE.search(findings)
    return causes, target.group(1) if target else None


def _last_action_at(state: State) -> datetime | None:
    handled = state.get("handled") or []
    try:
        return datetime.fromisoformat(handled[-1]["at"]) if handled else None
    except (KeyError, ValueError):
        return None


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
    # 진단에 몇 분이 걸리는 동안 HPA가 파드를 늘렸을 수 있다. 제안 직전의 서비스 목록으로 판단한다 (스펙 5.4.2)
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    observed = state.get("signals") or {}
    fault_task = asyncio.create_task(jev.classify_fault(observed)) if observed.get("metrics") else None
    services = parse_json(await box.call("propose_action", "business_get_service_inventory", {}, counts))
    fault = await fault_task if fault_task else None
    fault_note = ""
    if fault:
        # Jev는 장애 형태만 정한다. 확신도가 낮으면 GPT가 지금처럼 판정표로 직접 고르고, 판정은 기록만 남긴다.
        # Jev는 코드가 센 신호만 보고 GPT는 로그 원문까지 본다. Jev의 RB-STD-07은 "신호가 안 맞는다"일 뿐이라 넘기지 않는다
        # 진단이 처리할 원인을 적었으면 Jev 판정은 기록만 한다. 원인이 겹치면 Jev는 그중 하나만 고른다 (SPEC-compound-faults)
        applied = (fault["choice"] != "RB-STD-07" and fault["confidence"] >= jev.JEV_MIN_CONFIDENCE
                   and not state.get("target_clause"))
        await backend.emit(state["task_id"], "STEP", {"node": "propose_action", "fault": fault["choice"],
                                                       "confidence": round(fault["confidence"], 2), "applied": applied,
                                                       **({"target": state["target_clause"]}
                                                          if state.get("target_clause") else {})})
        if applied:
            fault_note = (f"장애 형태(Jev 판정, 확신도 {fault['confidence']:.0%}): {fault['choice']}. "
                          "1단계(장애 형태 정하기)는 이 판정을 따르고 2단계부터 진행한다.\n\n")
    context = {**state.get("context", {}), "services": services} if isinstance(services, list) \
        else state.get("context", {})
    state = {**state, "context": context, "tool_counts": counts}
    if state.get("causes"):
        # 원인 순위는 모델이 정한다. 코드는 막지 않고 사실 표와 함께 남긴다. 기준대로였는지는 E2E가 본다
        await backend.emit(state["task_id"], "STEP", {"node": "propose_action", "causes": state["causes"],
                                                       "target": state.get("target_clause"),
                                                       "facts": (state.get("signals") or {}).get("facts")})
    # 조치를 하지 않는 것이 맞는 경우는 HPA가 아직 최대치 아래에서 늘리는 중일 때뿐이다. 그때만 도구를 강제하지 않는다
    scaling = _autoscaler_scaling(context, (context.get("incident") or {}).get("service"))
    tools = [t for t in box.for_node("propose_action") if t.name != "business_get_service_inventory"]
    model = harness.chat_model(tools, harness.PROPOSE_REASONING_EFFORT, tool_choice=None if scaling else "required")
    messages = [
        SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("propose")),
        HumanMessage(f"{fault_note}진단 결과:\n{state['findings']}\n\n"
                     f"지표와 로그 신호(코드가 조회):\n{json.dumps(state.get('signals', {}), ensure_ascii=False)}\n\n"
                     f"이미 조치한 원인:\n{json.dumps(state.get('handled', []), ensure_ascii=False)}\n\n런북·계약 조항:\n"
                     f"{json.dumps(state.get('runbook', []), ensure_ascii=False)}\n\n"
                     f"고객사 컨텍스트:\n{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}"),
    ]
    ai: AIMessage = await model.ainvoke(messages)
    turns = state["turns"] + 1
    recent_change = bool((state.get("signals") or {}).get("recent_change"))
    sent_back: list[AnyMessage] = []
    for _ in range(PROPOSE_SEND_BACKS):
        # 조항과 맞지 않거나 할 수 없는 조치를 골랐다. 이유를 돌려주고 다시 고르게 한다 (판단은 여전히 모델이 한다).
        # 거부 이유는 쌓아서 넘긴다. 재시작을 거부하자 계약상 불가한 롤백을 고른 적이 있다 (b-pool E2E)
        problem = action_guards.first_problem(ai, context, state.get("signals") or {}, recent_change,
                                              state.get("target_clause"), state.get("handled") or [])
        if not problem or turns >= harness.MAX_TURNS:
            break
        call = ai.tool_calls[0]
        sent_back += [ai, ToolMessage(content=problem, tool_call_id=call["id"], status="error")] + [
            ToolMessage(content="이 호출은 실행하지 않았다", tool_call_id=c["id"], status="error") for c in ai.tool_calls[1:]]
        ai = await model.ainvoke(messages + sent_back)
        turns += 1
    base = {"context": context, "tool_counts": counts, "turns": turns}
    if not ai.tool_calls:
        return {**base, "proposed_action": None, "no_action_reason": str(ai.content)[:1000] or None}
    call = ai.tool_calls[0]
    required = {"business_execute_runbook_action": {"action", "service"},
                "business_escalate_incident": {"category", "reason"}}.get(call["name"], set())
    if not required <= call["args"].keys():
        # 필수 인자가 빠진 조치는 승인 요청으로 올리지 않는다. 사람이 빈 조치를 승인하게 만들지 않는다
        return {**base, "proposed_action": None}
    args = {**call["args"], "incident_id": state.get("incident_id")} if "incident_id" in call["args"] else call["args"]
    if call["name"] == "business_send_customer_notice":   # 조치로 고른 공지도 같은 규칙으로 확인한다
        args = {**args, "message": await customer_notice.finalize(str(args.get("message") or ""), state)}
    return {**base, "proposed_action": {"tool": call["name"], "args": args, "reason": str(ai.content)}}


PROPOSE_SEND_BACKS = 2   # 맞지 않는 조치를 돌려보내는 최대 횟수


def _autoscaler_scaling(context: dict, service: str | None) -> bool:
    """HPA가 부하를 보고 최소치보다 늘렸고, 아직 최대치에는 닿지 않았다 (스스로 더 늘릴 여지가 있다).

    최대치가 아니라는 것만으로는 부족하다. 풀 고갈·파드 멈춤처럼 부하와 무관한 장애에서도 HPA는 최대치 아래에 있다.
    """
    services = context.get("services") if isinstance(context.get("services"), list) else []
    item = next((s for s in services if isinstance(s, dict) and s.get("name") == service), None)
    hpa = (item or {}).get("autoscaling")
    return (isinstance(hpa, dict) and hpa.get("atMax") is False
            and (hpa.get("desiredReplicas") or 0) > (hpa.get("minReplicas") or 0))


def route_propose(state: State) -> Literal["approval", "report"]:
    return "approval" if state.get("proposed_action") else "report"


async def verify(state: State, runtime: Rt) -> dict:
    """조치 후 메트릭을 다시 보고 회복 여부를 판단한다 (Reflection).

    쿼리는 코드가 정해진 식으로 조회하고, 모델은 그 수치로 판단만 한다. 모델에게 PromQL을 맡겼을 때
    메트릭 이름을 지어내거나(reservation_api_requests_…) 조회 없이 결론을 낸 적이 있다 (docs/harness-spec.md).
    """
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    # 조치한 서비스만 본다. 서비스 조건이 없으면 같은 고객사의 다른 서비스 지표가 섞인다 (SPEC-limitations-fixes 4단계)
    service = (state["proposed_action"].get("args") or {}).get("service")
    obs = signals.observability(state.get("context", {}), service)
    queries = signals.verify_queries(obs, service) if obs else {}

    async def sample(name: str, sample_counts: dict[str, int]) -> float | None:
        return await signals.metric(box, queries[name], sample_counts) if queries else None

    await backend.emit(state["task_id"], "STEP", {"node": "verify", "waitSeconds": harness.VERIFY_SETTLE_SECONDS})
    await runtime.context.settle(harness.VERIFY_SETTLE_SECONDS)  # 조치가 메트릭에 반영될 시간
    error_rate = await sample("error_rate", counts)
    if error_rate is None and queries:  # 재시작한 파드의 메트릭이 아직 수집되지 않았다. 한 번만 더 기다린다
        await runtime.context.settle(harness.VERIFY_RETRY_SECONDS)
        error_rate = await sample("error_rate", counts)
    metrics = {"error_rate": error_rate, "p95_seconds": await sample("p95_seconds", counts),
               "window": "조치 직후 15초" if queries else f"{service}의 관측 정의가 없어 조회하지 못했다",
               "thresholds": {"error_rate": signals.ERROR_RATE_THRESHOLD, "p95_seconds": signals.P95_THRESHOLD_SECONDS}}
    by_threshold = signals.within_thresholds(metrics)
    if by_threshold:
        # 새 파드가 막 Ready가 된 순간에는 빈 파드가 대기 요청을 받아 p95가 잠깐 기준 아래로 내려간다.
        # 그 15초만 보고 회복으로 판정한 적이 있어(a-surge-max) 한 번 더 재서 둘 다 기준 안이어야 회복이다
        # 코드가 정한 재측정이라 같은 도구 한도(3회)에 넣지 않는다. 넣으면 재시도와 합쳐 한도를 넘는다
        await runtime.context.settle(harness.VERIFY_CONFIRM_SECONDS)
        confirm_counts: dict[str, int] = {}
        metrics["confirm"] = {"error_rate": await sample("error_rate", confirm_counts),
                              "p95_seconds": await sample("p95_seconds", confirm_counts),
                              "window": f"그 {harness.VERIFY_CONFIRM_SECONDS}초 뒤 15초"}
        by_threshold = signals.within_thresholds(metrics["confirm"])
    evidence = state["evidence"] + [{"tool": "grafana_query_prometheus", "args": queries,
                                     "summary": json.dumps(metrics, ensure_ascii=False)}]
    turns = state["turns"]
    if not queries:   # 잴 수 없으면 회복을 확인할 수 없다. 모델에게 수치 없이 판단을 맡기지 않는다
        recovered = False
        verdict = f"NOT_RECOVERED ({metrics['window']})"
    elif turns < harness.MAX_TURNS:
        runtime.context.check_time()
        ai: AIMessage = await harness.chat_model().ainvoke([
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("verify")),
            HumanMessage(f"실행한 조치: {json.dumps(state['proposed_action'], ensure_ascii=False)}\n"
                         f"진단 결과: {state['findings']}\n"
                         f"조치 후 메트릭: {json.dumps(metrics, ensure_ascii=False)}"),
        ])
        turns += 1
        verdict = str(ai.content)
        # 모델이 "회복 여부 확인: RECOVERED"처럼 첫 줄 형식을 어긴 적이 있다. 판정 단어를 어디서든 찾고, 없으면 기준값으로 정한다
        token = re.search(r"NOT_RECOVERED|RECOVERED", verdict.upper())
        recovered = token.group() == "RECOVERED" if token else by_threshold
        if recovered and "confirm" in metrics and not by_threshold:   # 다시 잰 값이 기준을 넘으면 회복이 아니다
            recovered = False
            verdict = f"NOT_RECOVERED (다시 잰 값이 기준 초과)\n{json.dumps(metrics['confirm'], ensure_ascii=False)}"
    else:  # 턴 한도를 다 썼으면 알림 기준으로 코드가 판단한다
        recovered = by_threshold
        verdict = f"{'RECOVERED' if recovered else 'NOT_RECOVERED'} (턴 한도로 기준값 판단)\n{json.dumps(metrics)}"
    update = {"recovered": recovered, "verification": verdict, "turns": turns, "tool_counts": counts,
              "evidence": evidence}
    if not recovered and obs and state.get("handled"):
        update["handled"] = await _recount_handled(box, state, obs, service)
    return update


CLEARED_RATIO = 0.2   # 조치 뒤 신호가 조치 전의 이 비율 미만이면 그 원인은 사라졌다고 본다


async def _recount_handled(box: ToolBox, state: State, obs: dict, service: str) -> list[dict[str, Any]]:
    """회복되지 않았으면 마지막으로 조치한 원인의 신호를 조치 뒤 구간으로 다시 센다. 사라졌으면 다른 원인이 남은 것이고,
    그대로면 조치가 듣지 않은 것이다. 판단은 다시 진단하는 모델이 하고, 같은 원인에 앱 조치를 또 하는 것은 검사가 막는다."""
    handled = list(state["handled"])
    last = dict(handled[-1])
    since = _last_action_at(state)
    if not last.get("signal") or since is None:
        return handled
    details = await signals.log_signal_details(box, obs, service, since, datetime.now(timezone.utc))
    after = (details.get(last["signal"]) or {}).get("count")
    before = last.get("signal_count_before")
    last["signal_count_after"] = after
    if isinstance(after, int) and isinstance(before, (int, float)):
        last["cleared"] = after < max(1, before * CLEARED_RATIO)
    handled[-1] = last
    return handled


def route_verify(state: State) -> Literal["report", "diagnose", "final_escalate"]:
    if state["recovered"]:
        return "report"
    return "diagnose" if state["attempts"] < 1 else "final_escalate"


async def bump_attempt(state: State) -> dict:
    return {"attempts": state["attempts"] + 1}


TOKEN_FLUSH_SECONDS = 0.1   # 보고서 스트리밍을 모아 보내는 간격


async def report(state: State, runtime: Rt) -> dict:
    """보고서를 TOKEN 이벤트로 흘려보내며 작성한다. 마지막의 '고객 공지 문안' 절은 notice_approval에서 쓴다.

    화면 요약 표에 쓸 분류는 보고서와 같은 입력으로 동시에 만든다.
    """
    payload = json.dumps({
        "type": state.get("type"), "incident_id": state.get("incident_id"),
        "request": state["question"], "plan": state.get("plan"), "findings": state.get("findings"),
        "evidence": state.get("evidence", []), "runbook": state.get("runbook", []),
        "past_incidents": state.get("past_incidents", []),
        "action": state.get("proposed_action"), "approvals": state.get("approvals", []),
        "recovered": state.get("recovered"), "verification": state.get("verification"),
        "causes": state.get("causes", []), "target_clause": state.get("target_clause"),
        "handled": state.get("handled", []), "cause_facts": (state.get("signals") or {}).get("facts"),
        "action_result": state.get("action_result"), "change": state.get("change"),
        "escalation": state.get("escalation_result"), "no_action_reason": state.get("no_action_reason"),
        "context": state.get("context", {}),
    }, ensure_ascii=False, default=str)

    information_question = state.get("type") == "ATTACHMENT" or (state.get("type") == "QUESTION" and state.get("incident_id") is None)

    async def write() -> str:
        instructions = (harness.prompt("answer") if information_question
                        else harness.prompt("report") + "\n\n" + harness.prompt("notice"))
        if state.get("type") == "ATTACHMENT":
            instructions = harness.prompt("attachment") + "\n첨부 분석 사실과 검색한 런북 근거를 구분하고 문서가 없으면 미대조임을 밝힌다. 한국어로 답한다."
        messages = [SystemMessage(harness.prompt("system") + "\n\n" + instructions),
                    HumanMessage(payload)]
        chunks: list[str] = []
        sent, flushed = 0, time.monotonic()
        async for chunk in harness.chat_model().astream(messages):
            if not chunk.content:
                continue
            chunks.append(str(chunk.content))
            # 토큰마다 보내면 보고서 하나에 backend 요청이 수백 번 나간다. 짧은 간격으로 모아 보낸다
            if time.monotonic() - flushed >= TOKEN_FLUSH_SECONDS:
                await backend.emit(state["task_id"], "TOKEN", {"text": "".join(chunks[sent:])})
                sent, flushed = len(chunks), time.monotonic()
        if sent < len(chunks):
            await backend.emit(state["task_id"], "TOKEN", {"text": "".join(chunks[sent:])})
        return "".join(chunks)

    await backend.emit(state["task_id"], "STEP", {"node": "report"})
    # 인시던트와 무관한 일반 질문에는 장애 원인 분류표가 의미가 없고, 오히려 없는 원인을 만들 수 있다.
    if information_question:
        text, classification = await write(), None
    else:
        text, classification = await asyncio.gather(write(), summary.classify(payload))
    body, _, notice = text.partition("## 고객 공지 문안")
    if notice.strip():   # 고객에게 나가는 글이라 규칙을 코드로 한 번 더 확인한다 (customer_notice)
        notice = await customer_notice.finalize(notice.strip(), state)
    return {"report": body.strip(), "notice_draft": notice.strip(), "classification": classification}


def route_report(state: State) -> Literal["notice_approval", "__end__"]:
    if state.get("type") == "ATTACHMENT":
        return END
    sent = (state.get("proposed_action") or {}).get("tool") == "business_send_customer_notice"
    return "notice_approval" if state.get("incident_id") and state.get("notice_draft") and not sent else END


# ── 사람의 결정을 기다리는 Node (interrupt) ───────────────────────


def _action_impact(args: dict, state: State) -> dict[str, Any]:
    """서비스 목록의 계산값을 승인 화면에 전달한다. 모델의 추정값은 넣지 않는다."""
    service = next((item for item in (state.get("context", {}).get("services") or [])
                    if item.get("name") == args.get("service")), None)
    if not service:
        return {}

    impact = {key: service[key] for key in ("runtime", "replicas", "readyReplicas", "safeMaxReplicas")
              if service.get(key) is not None}
    if args.get("action") == "restart_pods":
        zero_downtime = bool(service.get("zeroDowntimeRestart"))
        impact.update({"zeroDowntime": zero_downtime,
                       "restartMode": "ROLLING" if zero_downtime else "ALL_AT_ONCE"})
    elif args.get("action") == "scale_deployment" and args.get("replicas") is not None:
        impact["targetReplicas"] = args["replicas"]
    elif args.get("action") == "adjust_autoscaler":
        hpa = service.get("autoscaling") or {}
        impact.update({"autoscalerMaxReplicas": hpa.get("maxReplicas"), "targetMaxReplicas": args.get("max_replicas"),
                       "autoscalerMinReplicas": hpa.get("minReplicas")})
    return impact


def _approval_request(tool: str, args: dict, reason: str, state: State) -> dict:
    request = {"tool": tool, "args": args, "reason": reason,
               "approvalId": str(uuid5(NAMESPACE_URL, f"{state['task_id']}:{tool}:{len(state.get('approvals', []))}")),
               "evidence": state.get("evidence", [])[-5:], "runbook": state.get("runbook", [])[:3]}
    if tool == "business_execute_runbook_action":
        # 화면의 승인 카드에 반영 방식을 보여 준다. 방식은 backend가 정한 고객사 값이다
        request["changeMode"] = (state.get("context", {}).get("tenant") or {}).get("changeMode")
        request["impact"] = _action_impact(args, state)
    return request


async def register_incident(state: State, runtime: Rt) -> dict:
    """사람이 제보한 증상을 P2 인시던트로 남길지 승인받는다.

    이 node는 interrupt 앞에서 읽기나 쓰기를 하지 않는다. 재개할 때 node가 처음부터 실행되므로,
    승인 전에는 등록이 절대 일어나지 않고 승인 후 한 번만 MCP 변경 도구를 호출한다.
    """
    service = _reported_service(state)
    if service is None:  # route가 지키지만, UI/재개 상태가 달라도 임의 서비스로 등록하지 않는다
        return {"registration_rejected": True}
    args = {"service": service, "title": f"제보 기반 점검: {service}", "severity": "P2"}
    reason = (f"열린 인시던트는 없지만 '{service}'가 포함된 증상 제보가 들어왔다. "
              "자동 알림이 아닌 사람 제보이므로 P2 인시던트 등록 후 진단을 시작하려면 승인이 필요하다.")
    decision = interrupt(_approval_request("business_create_incident", args, reason, state))
    approvals = state["approvals"] + [{**decision, "tool": "business_create_incident"}]
    if decision.get("decision") != "APPROVE":
        return {"approvals": approvals, "registration_rejected": True}

    counts = dict(state["tool_counts"])
    result = await runtime.context.toolbox.call("register_incident", "business_create_incident", args, counts)
    created = parse_json(result)
    incident_id = created.get("incidentId") if result.status != "error" and isinstance(created, dict) else None
    if not isinstance(incident_id, int):
        # 등록 실패를 인시던트가 생긴 것처럼 진단하지 않는다. 실패 사실을 보고서로 남긴다.
        return {"approvals": approvals, "tool_counts": counts, "registration_rejected": True,
                "evidence": state["evidence"] + [{"tool": "business_create_incident", "status": "FAILED",
                                                       "summary": str(created)[:500]}]}
    return {"approvals": approvals, "tool_counts": counts, "incident_id": incident_id,
            "evidence": state["evidence"] + [{"tool": "business_create_incident", "status": "CREATED",
                                                   "summary": f"incidentId={incident_id}, service={service}"}]}


def route_register_incident(state: State) -> Literal["load_context", "no_issue"]:
    return "load_context" if state.get("incident_id") is not None else "no_issue"


async def approval(state: State) -> dict:
    action = state["proposed_action"]
    decision = interrupt(_approval_request(action["tool"], action["args"], action["reason"], state))
    return {"approvals": state["approvals"] + [{**decision, "tool": action["tool"]}]}


def route_after_runbook(state: State) -> Literal["report", "propose_action"]:
    return "report" if state.get("type") == "ATTACHMENT" else "propose_action"


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


def _escalation_args(state: State) -> dict[str, Any]:
    """흐름이 정하는 에스컬레이션의 분류와 이유. 받는 사람과 메시지는 backend가 정한다."""
    args: dict[str, Any] = {"incident_id": state["incident_id"]}
    action = (state.get("proposed_action") or {}).get("args") or {}
    change = state.get("change") or {}
    if change.get("result") == "TIMEOUT":
        args |= {"category": "CHANGE_TIMEOUT",
                 "reason": f"조치 PR이 기한 안에 병합되지 않음 ({change.get('prUrl')})"}
    elif not state.get("attempts"):
        args |= {"category": "P1", "reason": "P1 장애 발생"}
    else:
        handled = {h.get("clause") for h in state.get("handled") or []}
        remaining = [c for c in state.get("causes") or [] if c not in handled]
        args |= {"category": "NOT_RECOVERED",
                 "reason": f"{action.get('action') or '조치'} 후 다시 진단했지만 회복되지 않음"
                           + (f". 처리하지 못한 원인 후보: {', '.join(remaining)}" if remaining else "")}
    if action.get("service"):
        args["service"] = action["service"]
    return args


async def escalate(state: State, runtime: Rt) -> dict:
    """P1이거나 회복에 실패하거나 PR이 병합되지 않으면 담당자 호출을 제안한다. 승인되면 발송 대기열에 넣는다."""
    args = _escalation_args(state)
    decision = interrupt(_approval_request("business_escalate_incident", args, args["reason"], state))
    approvals = state["approvals"] + [{**decision, "tool": "business_escalate_incident"}]
    counts = dict(state["tool_counts"])
    update: dict[str, Any] = {"approvals": approvals, "tool_counts": counts}
    if decision.get("decision") == "APPROVE":
        result = await runtime.context.toolbox.call("escalate", "business_escalate_incident", args, counts,
                                                    incident_id=state["incident_id"])
        outcome = parse_json(result)
        update["escalation_result"] = (outcome if result.status != "error" and isinstance(outcome, dict)
                                       else {"status": "FAILED", "error": str(outcome)[:500]})
    return update


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
        ("analyze_attachments", attachments.analyze), ("no_issue", no_issue), ("register_incident", register_incident),
    ]:
        g.add_node(name, node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "load_context")
    g.add_conditional_edges("load_context", route_triage)          # triage
    g.add_conditional_edges("register_incident", route_register_incident)
    g.add_edge("escalate", "diagnose")
    g.add_conditional_edges("diagnose", route_diagnose)
    g.add_edge("analyze_attachments", "retrieve_runbook")
    g.add_conditional_edges("retrieve_runbook", route_after_runbook)
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
    g.add_edge("no_issue", END)
    return g.compile(checkpointer=checkpointer)

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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
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
    escalation_result: dict[str, Any] | None   # 에스컬레이션 발송 대기열 등록 결과 (받는 사람, 메시지, status QUEUED)
    no_action_reason: str | None    # 조치하지 않기로 한 이유 (HPA가 확장 중일 때만 생긴다)
    signals: dict[str, Any]         # 진단 시작 때 코드가 조회한 지표와 로그 신호 건수 (장애 형태 판정표의 입력)


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
    else:
        # 질문 작업: 인식된 장애가 있는지 본다. 조회에 실패하면(None) 장애가 없다고 단정하지 않는다
        incidents = parse_json(await box.call("load_context", "business_list_incidents", {"status": "OPEN"}, counts))
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


def route_triage(state: State) -> Literal["escalate", "diagnose", "retrieve_runbook", "no_issue"]:
    if _needs_document_lookup(state):
        return "retrieve_runbook"
    context = state.get("context", {})
    if not state.get("incident_id") and context.get("open_incidents") == []:
        return "no_issue"
    incident = context.get("incident") or {}
    return "escalate" if incident.get("severity") == "P1" and not state.get("approvals") else "diagnose"


async def no_issue(state: State) -> dict:
    """인식된 장애(열린 인시던트)가 없는 질문에는 진단·조치 없이 이상 없음으로 답한다 (docs/기능별-변경사항.md 7장)."""
    tenant = (state.get("context", {}).get("tenant") or {}).get("name") or state["tenant_id"]
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
    docs: list[dict[str, Any]] = []
    for args in searches:
        found = parse_json(await runtime.context.toolbox.call("retrieve_runbook", "rag_search_documents", args, counts))
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
    change = None
    if outcome and outcome["status"] == "PR_CREATED":
        change = {key: outcome.get(key) for key in ("action", "service", "changeMode", "prNumber", "prUrl", "files")}
    return {"evidence": evidence, "tool_counts": counts, "action_result": outcome, "change": change, **update}


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


_LOG_NUMBERS = re.compile(r"\d+")


# 장애 형태를 가르는 알려진 로그 신호 (공통 런북 RB-STD 조항의 증상). 코드는 있는지와 건수만 센다. 판단은 모델이 한다
LOG_SIGNALS = {
    "connection_pool_exhausted": "Connection is not available",   # RB-STD-01
    "worker_pool_saturated": "503 worker pool saturated",         # RB-STD-02
    "request_queue_backlog": "request queue backlog",             # RB-STD-03
    "external_api_timeout": "payment API timeout",                # RB-STD-04
    "slow_query": "slow query",                                   # RB-STD-05
}
LOG_WINDOW = timedelta(minutes=10)
LOG_LEAD = timedelta(minutes=2)   # 알림(for 30s + 평가 주기)은 이상 시작보다 1분 남짓 늦게 인시던트를 만든다


def _log_window_start(now: datetime, incident: dict[str, Any] | None) -> datetime:
    """최근 10분과 인시던트 시작 2분 전 중 늦은 시각. 같은 서비스에서 앞서 끝난 장애의 로그를 원인으로 읽지 않게 한다.

    E2E에서 B(파드 멈춤) 직후 B2(결제 지연)를 진단할 때, 10분 창에 남은 B의 `worker pool saturated` 로그를 보고
    재시작을 제안한 적이 있다 (docs/harness-spec.md).
    """
    start = now - LOG_WINDOW
    try:
        started = datetime.fromisoformat(str((incident or {})["startedAt"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return start
    return max(start, min(now - LOG_LEAD, started - LOG_LEAD))


async def _recent_log_patterns(box: ToolBox, counts: dict[str, int], incident_id: int | None,
                               incident: dict[str, Any] | None = None) -> tuple[str, dict]:
    """최근 ERROR·WARN 로그를 코드가 먼저 조회해 반복 메시지별로 센다. 구간은 _log_window_start.

    모델이 로그를 조회하지 않거나 단어로 미리 걸러 원인 로그(결제 API timeout 등)를 놓친 적이 있다 (docs/harness-spec.md).
    숫자를 지운 메시지를 패턴으로 묶는다. 마스킹과 tool_output 경계는 ToolBox가 이미 적용했다.
    """
    now = datetime.now(timezone.utc)
    args = {"logql": '{app="demo-app", level=~"ERROR|WARN"}', "limit": 200, "direction": "backward",
            "startRfc3339": _log_window_start(now, incident).isoformat(timespec="seconds"),
            "endRfc3339": now.isoformat(timespec="seconds")}
    # 200줄이면 결과 길이 제한(4000자)을 넘어 잘리고 JSON이 깨져 "로그 없음"으로 처리된 적이 있다.
    # 이 결과는 모델에 바로 가지 않고 아래에서 요약하므로 길이 제한을 넉넉히 둔다 (마스킹은 그대로)
    result = await box.call("diagnose", "grafana_query_loki_logs", args, counts, incident_id=incident_id,
                            max_chars=200_000)
    rows = parse_json(result)
    rows = rows.get("data", []) if isinstance(rows, dict) else rows if isinstance(rows, list) else []
    patterns: dict[str, dict[str, Any]] = {}
    for row in rows:
        line = str(row.get("line", "")).strip() if isinstance(row, dict) else ""
        if line:
            key = _LOG_NUMBERS.sub("#", line)[:200]
            item = patterns.setdefault(key, {"count": 0, "level": (row.get("labels") or {}).get("level"),
                                             "example": line[:300]})
            item["count"] += 1
    top = sorted(patterns.values(), key=lambda p: -p["count"])[:6]
    summary_text = (json.dumps(top, ensure_ascii=False) if top
                    else "조회 구간에 ERROR·WARN 로그 없음" if result.status != "error" else "로그 조회 실패")
    signals = {name: sum(p["count"] for key, p in patterns.items() if pattern in key)
               for name, pattern in LOG_SIGNALS.items()}
    return summary_text, {"tool": "grafana_query_loki_logs", "args": args, "status": result.status,
                          "summary": summary_text[:500], "signals": signals}


# 진단 시작 때 코드가 조회하는 기본 지표. 에러율은 RB-STD-02(5xx 급증)와 RB-STD-03(에러 없이 지연)을 가르는 기준인데,
# 모델이 에러율을 조회하지 않고 대기열 경고만 보고 RB-STD-02(재시작)로 판단한 적이 있다 (docs/harness-spec.md)
DIAGNOSE_QUERIES = {
    "error_rate": '(sum(rate(demo_orders_seconds_count{outcome="error"}[1m])) or vector(0))'
                  ' / sum(rate(demo_orders_seconds_count[1m]))',
    "p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[1m])))',
    "requests_per_second": 'sum(rate(demo_orders_seconds_count[1m]))',
}


async def diagnose(state: State, runtime: Rt) -> dict:
    prefetch: list[dict] = []
    if state.get("messages"):
        # 조치 후 재진단. 로그 신호와 지표를 지금 시점으로 다시 센다. 첫 진단 때는 경고 로그가 아직 Loki에 들어오지
        # 않아 0건이었고, 그 값을 그대로 쓰자 장애 형태 표에 맞는 행이 없어 원인 미확정이 된 적이 있다
        # (A사 a-surge-max, docs/harness-spec.md). 정해진 조회라 모델의 탐색 한도에 넣지 않는다
        prefetch_counts: dict[str, int] = {}
        logs, logged = await _recent_log_patterns(runtime.context.toolbox, prefetch_counts, state.get("incident_id"),
                                                  state.get("context", {}).get("incident"))
        metrics = {name: await _metric(runtime.context.toolbox, expr, prefetch_counts, node="diagnose")
                   for name, expr in DIAGNOSE_QUERIES.items()}
        state = {**state, "signals": {**(state.get("signals") or {}), "metrics": metrics,
                                      "logs": logged.get("signals", {})}}
        prefetch = [logged, {"tool": "grafana_query_prometheus", "args": DIAGNOSE_QUERIES,
                             "summary": json.dumps(metrics, ensure_ascii=False)}]
        intro = [HumanMessage(f"조치 후에도 회복되지 않았다. 조치 이후 시간대를 다시 조사한다.\n\n"
                              f"지금 시점의 ERROR·WARN 로그 반복 메시지(코드가 조회, 많은 순):\n{harness.as_untrusted(logs)}\n\n"
                              f"현재 지표(코드가 조회, 최근 1분):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                              f"알려진 로그 신호 건수(코드가 셈):\n{json.dumps(logged.get('signals', {}), ensure_ascii=False)}")]
    else:
        counts = dict(state["tool_counts"])
        logs, logged = await _recent_log_patterns(runtime.context.toolbox, counts, state.get("incident_id"),
                                                  state.get("context", {}).get("incident"))
        # 정해진 쿼리라 모델의 탐색 한도(같은 도구 3번)에 넣지 않는다. 넣으면 모델이 Prometheus를 더 부를 수 없다
        prefetch_counts: dict[str, int] = {}
        metrics = {name: await _metric(runtime.context.toolbox, expr, prefetch_counts, node="diagnose")
                   for name, expr in DIAGNOSE_QUERIES.items()}
        # 직전 변경 여부는 RB-STD-01(롤백)과 RB-STD-02(재시작)를 가른다. 모델이 조회하지 않고 "직전 변경 없음"이라고
        # 단정해 롤백 대신 재시작을 제안한 적이 있어 코드가 미리 조회한다 (docs/harness-spec.md)
        service = (state.get("context", {}).get("incident") or {}).get("service")
        changes = parse_json(await runtime.context.toolbox.call(
            "diagnose", "business_list_recent_changes", {"service": service}, prefetch_counts)) if service else None
        started_at = (state.get("context", {}).get("incident") or {}).get("startedAt")
        state = {**state, "tool_counts": counts, "signals": {"metrics": metrics, "logs": logged.get("signals", {}),
                                                             "recent_change": _recent_change(changes, started_at)}}
        prefetch = [logged, {"tool": "grafana_query_prometheus", "args": DIAGNOSE_QUERIES,
                             "summary": json.dumps(metrics, ensure_ascii=False)},
                    {"tool": "business_list_recent_changes", "args": {"service": service},
                     "summary": json.dumps(changes, ensure_ascii=False, default=str)[:500]}]
        intro = [
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("diagnose")),
            HumanMessage(f"요청: {state['question']}\n\n고객사 컨텍스트(backend 계산값):\n"
                         f"{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}\n\n"
                         f"{logged['args']['startRfc3339']} 이후 ERROR·WARN 로그의 반복 메시지"
                         f"(코드가 조회, 많은 순. 인시던트 시작 2분 전부터, 최대 10분):\n"
                         f"{harness.as_untrusted(logs)}\n\n"
                         f"현재 지표(코드가 조회, 최근 1분. 에러율은 0~1):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                         f"알려진 로그 신호 건수(코드가 셈, 0이면 없음):\n{json.dumps(logged.get('signals', {}), ensure_ascii=False)}\n\n"
                         f"인시던트 시작: {(state.get('context', {}).get('incident') or {}).get('startedAt')}\n"
                         f"최근 배포·설정 변경(코드가 조회, 최신순):\n"
                         f"{harness.as_untrusted(json.dumps(changes, ensure_ascii=False, default=str)[:3000])}"),
        ]
    history = list(state.get("messages", [])) + intro
    new, turns, counts, evidence = await _tool_loop("diagnose", state, runtime, history, harness.MAX_TURNS,
                                                    force_first_tool=harness.DIAGNOSE_FORCE_FIRST_TOOL)
    findings = _final_text(new) or "원인 미확정: 턴 한도 안에 근거를 모으지 못했다"
    if not any(isinstance(m, ToolMessage) and m.status != "error" for m in new):
        # 근거 없는 글(조사 계획 등)이 런북 검색어와 조치 제안의 입력이 되지 않게 한다
        findings = "원인 미확정: 도구로 근거를 모으지 못했다"
    update = {"messages": intro + new, "findings": findings, "turns": turns, "tool_counts": counts,
              "evidence": state["evidence"] + prefetch + evidence}
    if state.get("signals"):
        update["signals"] = state["signals"]
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
    # 진단에 몇 분이 걸리는 동안 HPA가 파드를 늘렸을 수 있다. 제안 직전의 서비스 목록으로 판단한다 (스펙 5.4.2)
    box, counts = runtime.context.toolbox, dict(state["tool_counts"])
    services = parse_json(await box.call("propose_action", "business_get_service_inventory", {}, counts))
    context = {**state.get("context", {}), "services": services} if isinstance(services, list) \
        else state.get("context", {})
    state = {**state, "context": context, "tool_counts": counts}
    # 조치를 하지 않는 것이 맞는 경우는 HPA가 아직 최대치 아래에서 늘리는 중일 때뿐이다. 그때만 도구를 강제하지 않는다
    scaling = _autoscaler_scaling(context, (context.get("incident") or {}).get("service"))
    tools = [t for t in box.for_node("propose_action") if t.name != "business_get_service_inventory"]
    model = harness.chat_model(tools, harness.PROPOSE_REASONING_EFFORT, tool_choice=None if scaling else "required")
    messages = [
        SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("propose")),
        HumanMessage(f"진단 결과:\n{state['findings']}\n\n"
                     f"지표와 로그 신호(코드가 조회):\n{json.dumps(state.get('signals', {}), ensure_ascii=False)}\n\n런북·계약 조항:\n"
                     f"{json.dumps(state.get('runbook', []), ensure_ascii=False)}\n\n"
                     f"고객사 컨텍스트:\n{json.dumps(state.get('context', {}), ensure_ascii=False, default=str)}"),
    ]
    ai: AIMessage = await model.ainvoke(messages)
    turns = state["turns"] + 1
    mismatch = _clause_mismatch(ai, bool((state.get("signals") or {}).get("recent_change")))
    if mismatch and turns < harness.MAX_TURNS:
        # 조항과 맞지 않는 조치를 골랐다. 이유를 돌려주고 한 번만 다시 고르게 한다 (판단은 여전히 모델이 한다)
        call = ai.tool_calls[0]
        ai = await model.ainvoke(messages + [ai, ToolMessage(content=mismatch, tool_call_id=call["id"], status="error")]
                                 + [ToolMessage(content="이 호출은 실행하지 않았다", tool_call_id=c["id"], status="error")
                                    for c in ai.tool_calls[1:]])
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
    return {**base, "proposed_action": {"tool": call["name"], "args": args, "reason": str(ai.content)}}


# 공통 런북 조항별로 고칠 수 있는 조치 (data/documents/_common/runbook-standard.md). 에스컬레이션·공지는 언제나 된다.
# 할 수 없으면 다른 조치로 대신하지 않는다: B사 급증에서 확장이 계약상 불가하자 효과 없는 재시작을 제안한 적이 있다
CLAUSE_ACTIONS: dict[str, set[str]] = {
    "RB-STD-01": {"rollback_deployment", "restart_pods"},   # 직전 변경이 없으면 RB-STD-02의 재시작
    "RB-STD-02": {"restart_pods"},
    "RB-STD-03": {"scale_deployment", "adjust_autoscaler"},
    "RB-STD-04": set(), "RB-STD-05": set(), "RB-STD-06": set(), "RB-STD-07": set(),
}
_CLAUSE = re.compile(r"RB-STD-0\d")


RECENT_CHANGE_MINUTES = 15   # 이상 시작 전 이 시간 안의 배포·설정 변경을 "직전 변경"으로 본다 (RB-STD-01)


def _recent_change(changes: Any, started_at: str | None) -> bool | None:
    """인시던트 시작 전 RECENT_CHANGE_MINUTES 안에 배포·설정 변경이 있었나. 판단할 수 없으면 None."""
    if not isinstance(changes, dict) or not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        times = [datetime.fromisoformat(str(c.get("createdAt") or c.get("date")).replace("Z", "+00:00"))
                 for c in (changes.get("revisions") or changes.get("commits") or []) if isinstance(c, dict)]
    except ValueError:
        return None
    return any(start - timedelta(minutes=RECENT_CHANGE_MINUTES) <= t <= start for t in times)


def _clause_mismatch(ai: AIMessage, recent_change: bool = False) -> str | None:
    """제안 이유가 인용한 공통 런북 조항과 고른 조치가 맞지 않으면 그 이유. 조항을 인용하지 않았으면 검사하지 않는다.

    RB-STD-01의 재시작은 직전 변경이 없을 때만이다. 직전 변경이 있는데 롤백이 계약상 불가하자 재시작으로
    대신한 적이 있다 (B사 풀 고갈, docs/harness-spec.md).
    """
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_execute_runbook_action":
        return None
    args = ai.tool_calls[0]["args"]
    clauses = sorted(set(_CLAUSE.findall(f"{args.get('reason', '')} {ai.content}")) & CLAUSE_ACTIONS.keys())
    fits = {c: CLAUSE_ACTIONS[c] - ({"restart_pods"} if c == "RB-STD-01" and recent_change else set())
            for c in clauses}
    if not clauses or any(args.get("action") in fits[c] for c in clauses):
        return None
    allowed = sorted({a for c in clauses for a in fits[c]})
    return (f"거부: {', '.join(clauses)}의 조치는 {allowed or '앱 조치 없음'}이다. '{args.get('action')}'는 이 장애 형태를 "
            "고치지 못한다. 그 조치가 supportedActions에 없거나 상한에 막히면 다른 조치로 대신하지 말고 "
            "business_escalate_incident(category=\"ACTION_BLOCKED\", required_action=…, blocked_by=…)를 호출한다. "
            "외부·DB 원인이면 공지 또는 DEPENDENCY 에스컬레이션이다.")


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


# 회복 확인의 표준 메트릭. 알림 규칙(infra/prometheus/alert-rules.yml)과 같은 기준을 조치 직후 15초 창으로 본다.
# 에러가 하나도 없으면 에러 시계열이 없으므로 `or vector(0)`을 붙인다. 요청 자체가 없으면 결과가 비어 None이 된다.
VERIFY_QUERIES = {
    "error_rate": '(sum(rate(demo_orders_seconds_count{outcome="error"}[15s])) or vector(0))'
                  ' / sum(rate(demo_orders_seconds_count[15s]))',
    "p95_seconds": 'histogram_quantile(0.95, sum by (le) (rate(demo_orders_seconds_bucket[15s])))',
}
ERROR_RATE_THRESHOLD = 0.2
P95_THRESHOLD_SECONDS = 1.0


async def _metric(box: ToolBox, expr: str, counts: dict[str, int], node: str = "verify") -> float | None:
    result = parse_json(await box.call(node, "grafana_query_prometheus",
                                       {"expr": expr, "queryType": "instant", "endTime": "now"}, counts))
    try:
        return float(result["data"][0]["value"][1])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def _within_thresholds(sample: dict[str, Any]) -> bool:
    """알림 기준 안이다. 에러율이 없으면(요청 메트릭 미수집) 기준 안으로 보지 않고, p95가 없으면 지연은 보지 않는다."""
    error_rate = sample.get("error_rate")
    return (error_rate is not None and error_rate < ERROR_RATE_THRESHOLD
            and (sample.get("p95_seconds") or 0) < P95_THRESHOLD_SECONDS)


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
    by_threshold = _within_thresholds(metrics)
    if by_threshold:
        # 새 파드가 막 Ready가 된 순간에는 빈 파드가 대기 요청을 받아 p95가 잠깐 기준 아래로 내려간다.
        # 그 15초만 보고 회복으로 판정한 적이 있어(a-surge-max) 한 번 더 재서 둘 다 기준 안이어야 회복이다
        # 코드가 정한 재측정이라 같은 도구 한도(3회)에 넣지 않는다. 넣으면 재시도와 합쳐 한도를 넘는다
        await runtime.context.settle(harness.VERIFY_CONFIRM_SECONDS)
        confirm_counts: dict[str, int] = {}
        metrics["confirm"] = {"error_rate": await _metric(box, VERIFY_QUERIES["error_rate"], confirm_counts),
                              "p95_seconds": await _metric(box, VERIFY_QUERIES["p95_seconds"], confirm_counts),
                              "window": f"그 {harness.VERIFY_CONFIRM_SECONDS}초 뒤 15초"}
        by_threshold = _within_thresholds(metrics["confirm"])
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
        # 모델이 "회복 여부 확인: RECOVERED"처럼 첫 줄 형식을 어긴 적이 있다. 판정 단어를 어디서든 찾고, 없으면 기준값으로 정한다
        token = re.search(r"NOT_RECOVERED|RECOVERED", verdict.upper())
        recovered = token.group() == "RECOVERED" if token else by_threshold
        if recovered and "confirm" in metrics and not by_threshold:   # 다시 잰 값이 기준을 넘으면 회복이 아니다
            recovered = False
            verdict = f"NOT_RECOVERED (다시 잰 값이 기준 초과)\n{json.dumps(metrics['confirm'], ensure_ascii=False)}"
    else:  # 턴 한도를 다 썼으면 알림 기준으로 코드가 판단한다
        recovered = by_threshold
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
        "escalation": state.get("escalation_result"), "no_action_reason": state.get("no_action_reason"),
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
        args |= {"category": "NOT_RECOVERED",
                 "reason": f"{action.get('action') or '조치'} 후 다시 진단했지만 회복되지 않음"}
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
        ("no_issue", no_issue),
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
    g.add_edge("no_issue", END)
    return g.compile(checkpointer=checkpointer)

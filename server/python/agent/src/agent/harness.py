"""Harness: 모델을 감싸 실행·한도·기록·복구를 통제한다 (기획안 8장, PDF 표 6-3).

값을 바꾸면 docs/harness-spec.md에 바꾼 값과 이유를 남긴다.
"""

import re
from functools import cache
from pathlib import Path
from typing import Any

from langchain_core.messages import AnyMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from agent.config import settings

# ── Agent Loop ────────────────────────────────────────────────
MAX_TURNS = 8            # 작업 하나에서 모델을 부르는 최대 횟수
TASK_SECONDS = 120       # 실행 시간 한도. 승인·반영 대기와 회복 확인 대기(VERIFY_SETTLE_SECONDS)는 빼고 잰다
MAX_SAME_TOOL = 3        # 같은 Node 안에서 같은 도구 호출 한도
# 조치가 메트릭에 반영될 때까지 기다리는 시간. graph.VERIFY_QUERIES의 rate 창(15s)보다 길어야 한다
VERIFY_SETTLE_SECONDS = 20
VERIFY_RETRY_SECONDS = 10   # 조치 후 메트릭이 아직 없으면 한 번 더 기다리는 시간 (재시작한 파드의 첫 수집)
# gpt-5-mini가 조치 도구를 호출하지 않고 권고문만 쓰는 경우가 있었다 (low 2/2, medium 1/5, docs/harness-spec.md).
# 그래서 propose_action은 도구 호출을 강제하고, 어떤 도구를 고를지는 medium으로 판단한다
PROPOSE_REASONING_EFFORT = "medium"
# 진단 첫 턴에 계획 글만 쓰고 도구 없이 끝나면 그 글이 진단 결과가 되었다. 첫 턴만 도구 호출을 강제한다
DIAGNOSE_FORCE_FIRST_TOOL = settings.diagnose_force_first_tool

# ── Tools / Context ───────────────────────────────────────────
TOOL_RESULT_CHARS = 4_000      # 도구 결과는 이 길이로 자른다
CONTEXT_CHARS = 24_000         # 대화가 이 길이를 넘으면 오래된 도구 결과를 줄인다 (Token 예산 근사치)

# ── Tools: Node별 허용목록 ─────────────────────────────────────
# 도구 이름 앞의 business_/rag_/grafana_ 는 MCP Client가 Server 이름으로 붙인다
NODE_TOOLS: dict[str, set[str]] = {
    "load_context": {"business_get_tenant_profile", "business_get_sla_terms", "business_get_service_inventory",
                     "business_list_incidents"},
    "diagnose": {"grafana_query_prometheus", "grafana_query_prometheus_histogram",
                 "grafana_list_prometheus_label_values", "grafana_query_loki_logs", "grafana_query_loki_patterns",
                 "grafana_query_loki_stats", "business_list_incidents", "business_get_service_inventory",
                 "business_list_recent_changes"},
    "retrieve_runbook": {"rag_search_documents"},
    "propose_action": {"business_execute_runbook_action", "business_send_customer_notice",
                       "business_get_service_inventory",   # 제안 직전에 코드가 HPA 상태를 새로 읽는다
                       "business_escalate_incident"},   # 계약·구조상 할 수 없으면 조치 대신 에스컬레이션 (RB-STD-05~07)
    "escalate": {"business_escalate_incident"},
    "verify": {"grafana_query_prometheus"},   # 코드가 정해진 쿼리로만 호출한다 (graph.VERIFY_QUERIES)
    "notice": {"business_send_customer_notice"},
}

# 사람이 승인해야 실행되는 변경 도구 (MCP 도구의 destructiveHint와 같다)
DESTRUCTIVE_TOOLS = {
    "business_create_incident", "business_escalate_incident", "business_execute_runbook_action",
    "business_send_customer_notice", "business_confirm_sla_credit",
}


class HarnessLimit(Exception):
    """턴·시간·도구 한도를 넘었다."""


# ── Guardrails: 민감정보 마스킹 ─────────────────────────────────
_MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"), "[PHONE]"),
    (re.compile(r"(?i)\bbearer\s+[\w.~+/-]+=*"), "Bearer [TOKEN]"),
    (re.compile(r"\b(sk|pk|ghp|glsa)_[A-Za-z0-9_-]{8,}\b"), "[SECRET]"),
]


def mask(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


def truncate(text: str, limit: int = TOOL_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…(잘림: 원래 {len(text)}자)"


def as_untrusted(text: str) -> str:
    """도구 결과(로그 등)에 섞인 지시문을 따르지 않도록 데이터 경계로 감싼다. prompts/system.md와 짝이다."""
    return f"<tool_output>\n{text}\n</tool_output>"


# ── Guardrails: 테넌트 격리 ────────────────────────────────────
def enforce_tenant_datasource(tool: BaseTool, args: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """grafana 조회 도구의 데이터소스를 현재 고객사 것으로 강제한다. 쿼리 문자열은 검사하지 않는다.

    데이터소스 UID는 infra/grafana/provisioning/datasources/datasources.yml과 같다 (prom-<tenant>, loki-<tenant>).
    인자 이름은 mcp-grafana 버전마다 다를 수 있어서 도구 스키마에서 'datasource'가 들어간 이름을 찾는다.
    """
    if not tool.name.startswith("grafana_"):
        return args
    uid = f"loki-{tenant_id}" if "loki" in tool.name else f"prom-{tenant_id}"
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else tool.args_schema.model_json_schema()
    keys = [k for k in schema.get("properties", {}) if "datasource" in k.lower()]
    if not keys:
        raise HarnessLimit(f"{tool.name}: 데이터소스 인자를 찾지 못해 고객사 격리를 보장할 수 없다")
    return {**args, keys[0]: uid}


# ── Context: Token 예산 ───────────────────────────────────────
def compact(messages: list[AnyMessage]) -> list[AnyMessage]:
    """대화가 예산을 넘으면 오래된 도구 결과부터 앞부분만 남긴다. 도구 호출과 결과의 짝은 유지한다.

    TODO: 줄인 결과를 모델로 요약해 한 메시지로 합친다 (PDF 기준: 넘으면 오래된 대화를 요약)
    """
    total = sum(len(str(m.content)) for m in messages)
    compacted = list(messages)
    for i, message in enumerate(compacted[:-4]):
        if total <= CONTEXT_CHARS:
            break
        if isinstance(message, ToolMessage) and len(str(message.content)) > 300:
            short = str(message.content)[:300] + "\n…(예산 초과로 줄임)"
            total -= len(str(message.content)) - len(short)
            compacted[i] = message.model_copy(update={"content": short})
    return compacted


# ── Recovery: 재시도와 대체 모델 ───────────────────────────────
_RETRYABLE = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)


def chat_model(tools: list[BaseTool] | None = None, reasoning_effort: str | None = None,
               tool_choice: str | None = None) -> Runnable:
    """429·5xx는 2회 재시도(총 3회)하고, 그래도 실패하면 대체 모델로 넘어간다.

    reasoning_effort를 주지 않으면 LLM_REASONING_EFFORT를 쓴다. tool_choice="required"면 도구를 반드시 고른다.
    """
    return _resilient(lambda llm: llm.bind_tools(tools, tool_choice=tool_choice) if tools else llm,
                      reasoning_effort)


def structured_model(schema: type) -> Runnable:
    """응답을 schema(pydantic 모델)로 강제한다. 재시도와 대체 모델은 chat_model과 같다."""
    return _resilient(lambda llm: llm.with_structured_output(schema))


def _resilient(wrap, reasoning_effort: str | None = None) -> Runnable:
    effort = reasoning_effort or settings.reasoning_effort or None

    def build(model: str) -> Runnable:
        llm = ChatOpenAI(model=model, max_retries=0, timeout=30, reasoning_effort=effort)
        return wrap(llm).with_retry(retry_if_exception_type=_RETRYABLE, stop_after_attempt=3)

    return build(settings.llm_model).with_fallbacks([build(settings.fallback_model)])


# ── Instructions: prompts/*.md ────────────────────────────────
@cache
def prompt(name: str) -> str:
    return (Path(settings.prompts_dir) / f"{name}.md").read_text(encoding="utf-8")

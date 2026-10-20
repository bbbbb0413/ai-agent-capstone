"""고객 공지 문안 검사 (prompts/notice.md의 규칙을 코드로 확인한다).

공지는 고객사 담당자에게 나가는 글이라 보고서보다 엄격하게 본다. 모델이 지시문 문장을 그대로 옮기거나
("정상화되었을 경우 안내: …"), 내부 처리 실패("에스컬레이션 요청은 형식 오류로 실패")나 다른 언어 단어를
섞은 적이 있다 (a-surge-max, docs/harness-spec.md). 문제가 있으면 한 번 고쳐 쓰게 하고, 그래도 문제가 있으면
상태값으로 만든 기본 문안을 쓴다. 어느 경우든 발송 전에 MSP 관리자가 승인 카드에서 확인한다.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from agent import harness

log = logging.getLogger(__name__)

MAX_CHARS = 400
# 한국어 낱말은 그대로 찾고, 영문 약어는 낱말 경계로 찾는다 ("PR"이 다른 낱말 안에 있을 때 걸리지 않게)
# 고객사 식별자(tenant-a)와 지표 용어(p95, 15초 창)도 고객이 읽는 말이 아니다
# 승인은 MSP·고객사 사이의 처리 절차이고, 고객 담당자에게는 발송 대기열 에스컬레이션으로 따로 간다
INTERNAL_TERMS = ("형식 오류", "도구", "대기열", "에스컬레이션", "재시도", "체크포인트", "프롬프트", "런북",
                  "tenant-", "p95", "초 창", "DEGRADED", "autoscaler", "승인", "타임아웃")
INTERNAL_ABBREVIATIONS = ("PR", "RB-STD", "Agent", "MCP", "LLM", "MSP")
_HOSTNAME = re.compile(r"\b(?:[\w-]+\.)+(?:com|net|io|kr|svc|local)\b")
# 회복 전 공지의 다음 안내 시각: "30분 안에", "1시간 이내", "15시까지", "15:30에"
_NEXT_NOTICE = re.compile(r"\d+\s*(?:분|시간)\s*(?:안에|이내|후|뒤)|\d{1,2}(?::\d{2}|시(?:\s*\d{1,2}분)?)\s*(?:까지|에)\s")
# 지시문을 옮긴 말과, 공지 앞에 붙인 머리말("다음은 공지 초안", "발송해 주세요")
TEMPLATE_PHRASES = ("정상화되었을 경우", "회복되지 않았을 경우", "안내 용도", "다음 안내 시각을 구체적으로", "문장 이내",
                    "초안", "공지안", "문안", "발송해 주세요", "수정해 주세요", "내부 검토")
_LIST_LINE = re.compile(r"^\s*(?:[-*#]|\d+[.)])\s*")
KST = ZoneInfo("Asia/Seoul")


def _hangul(c: str) -> bool:
    return "가" <= c <= "힣" or "ᄀ" <= c <= "ᇿ" or "㄰" <= c <= "㆏"


def problems(text: str, recovered: bool | None = None) -> list[str]:
    """공지 규칙에 어긋나는 점. 비어 있으면 규칙을 지킨 것이다. recovered=False면 다음 안내 시각도 본다."""
    found: list[str] = []
    if not text.strip():
        return ["비어 있다"]
    foreign = sorted({c for c in text if c.isalpha() and not c.isascii() and not _hangul(c)})
    if foreign:
        found.append(f"한글·영문이 아닌 문자: {''.join(foreign[:10])}")
    found += [f"내부 용어: {t}" for t in INTERNAL_TERMS if t in text]
    found += [f"내부 용어: {t}" for t in INTERNAL_ABBREVIATIONS if re.search(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", text)]
    found += [f"지시문 문구: {p}" for p in TEMPLATE_PHRASES if p in text]
    if host := _HOSTNAME.search(text):
        found.append(f"내부 주소: {host.group()}")
    if recovered is False and not _NEXT_NOTICE.search(text):
        found.append("회복 전인데 구체적인 다음 안내 시각이 없다 (예: 30분 안에)")
    if any(_LIST_LINE.match(line) for line in text.splitlines() if line.strip()):
        found.append("목록·제목 형식")
    if len(text) > MAX_CHARS:
        found.append(f"{MAX_CHARS}자 초과 ({len(text)}자)")
    return found


def _facts(state: dict[str, Any]) -> dict[str, Any]:
    incident = (state.get("context") or {}).get("incident") or {}
    started = incident.get("startedAt")
    try:
        started_kst = datetime.fromisoformat(str(started).replace("Z", "+00:00")).astimezone(KST).strftime("%H:%M")
    except ValueError:
        started_kst = None
    return {"service": incident.get("service"), "started_at_kst": started_kst, "recovered": state.get("recovered") is True}


def fallback(state: dict[str, Any]) -> str:
    """모델 문안을 쓸 수 없을 때의 기본 문안. 상태값만 쓴다."""
    facts = _facts(state)
    subject = f"{facts['service']} 서비스" if facts["service"] else "서비스"
    since = f"오늘 {facts['started_at_kst']}부터 " if facts["started_at_kst"] else ""
    if facts["recovered"]:
        return (f"{subject}에서 {since}이용 지연 또는 오류가 있었으며 현재 정상화되었습니다. "
                "원인 분석 결과를 정리해 다시 안내드리겠습니다.")
    return (f"{subject}에서 {since}이용 지연 또는 오류가 발생해 복구 작업을 진행하고 있습니다. "
            "30분 안에 진행 상황을 다시 안내드리겠습니다.")


async def finalize(draft: str, state: dict[str, Any]) -> str:
    """규칙을 지킨 공지를 돌려준다: 그대로, 한 번 고쳐 쓴 것, 또는 기본 문안."""
    recovered = _facts(state)["recovered"]
    found = problems(draft, recovered)
    if not found:
        return draft
    log.warning("task %s notice draft rejected: %s", state.get("task_id"), found)
    try:
        ai = await harness.chat_model().ainvoke([
            SystemMessage(harness.prompt("system") + "\n\n" + harness.prompt("notice")
                          + "\n\n초안이 위 규칙을 어겼다. 규칙대로 고친 공지 본문만 답한다."),
            HumanMessage(json.dumps({"draft": draft, "problems": found, "facts": _facts(state)}, ensure_ascii=False)),
        ])
        rewritten = str(ai.content).strip()
        if not (left := problems(rewritten, recovered)):
            return rewritten
        log.warning("task %s notice rewrite rejected: %s", state.get("task_id"), left)
    except Exception:  # noqa: BLE001 — 고쳐 쓰기에 실패해도 기본 문안으로 공지는 나간다
        log.warning("task %s notice rewrite failed", state.get("task_id"), exc_info=True)
    return fallback(state)

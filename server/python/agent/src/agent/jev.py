"""Jev(TypeSafe System One)로 장애 형태(공통 런북 조항)를 고른다. 조치·인자·이유는 여전히 GPT가 정한다 (docs/jev.md)."""

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

JEV_MODEL = os.getenv("TYPESAFE_MODEL", "jev-latest")
JEV_TIMEOUT_SECONDS = float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "5"))
JEV_MIN_CONFIDENCE = float(os.getenv("TYPESAFE_MIN_CONFIDENCE", "0.9"))   # 이보다 낮으면 GPT에 넘기지 않고 기록만 한다

# action_guards.signal_clause, signals.P95_THRESHOLD_SECONDS와 같은 기준이어야 판정을 서로 비교할 수 있다
ERROR_RATE_HIGH = 0.05
P95_SLOW_SECONDS = 1.0

LOG_LABELS = {
    "connection_pool_exhausted": "DB 커넥션 풀 고갈 (Connection is not available)",
    "worker_pool_saturated": "작업 스레드 고갈 (503 worker pool saturated)",
    "request_queue_backlog": "처리 슬롯 대기 (request queue backlog)",
    "external_api_timeout": "외부 API 시간 초과 (payment API timeout)",
    "slow_query": "DB 지연 (slow query)",
}

# RB-STD-06은 장애 형태가 아니라 조치를 할 수 있는지에 관한 조항이라 선택지에 넣지 않는다
FAULT_CRITERIA = {
    "RB-STD-01": "설정 변경으로 인한 DB 커넥션 풀 고갈. 5xx 에러율이 높고 커넥션 풀 고갈 로그가 있으며 직전에 배포·설정 변경이 있었다. DB 지연 로그는 없다",
    "RB-STD-02": "작업 스레드 고갈(파드 멈춤). 5xx 에러율이 높고 작업 스레드 고갈 로그가 있다. 직전 변경이 없다",
    "RB-STD-03": "트래픽 급증(앱 처리 용량 부족). 5xx 에러율은 정상인데 응답이 느리고 처리 슬롯 대기 로그가 있다. 외부 API 시간 초과와 DB 지연 로그는 없다",
    "RB-STD-04": "외부 의존 서비스 지연. 응답이 느리고 외부 API 시간 초과 로그가 있다. 처리 슬롯 대기와 DB 지연 로그는 없다",
    "RB-STD-05": "의존 대상(DB) 자체 장애. 응답이 느리거나 5xx가 있고 DB 지연 로그가 있다. 커넥션 풀 고갈 로그가 같이 있어도 DB 지연이 있으면 이 조항이다",
    "RB-STD-07": "원인 미확정. 지표·로그·변경 이력이 위 장애 형태 중 어느 것과도 맞지 않는다",
}

FAULT_QUESTION = {
    "type": "choice",
    "instructions": "운영 지표와 로그 신호를 보고 이 장애에 해당하는 공통 런북 조항을 하나 고른다",
    "criteria": FAULT_CRITERIA,
}


def _level(value: Any, threshold: float, high: str, low: str, fmt: str) -> str:
    if not isinstance(value, (int, float)):
        return "알 수 없음"
    word, sign = (high, "이상") if value >= threshold else (low, "미만")
    if fmt == "percent":
        return f"{word} ({value:.1%}, 기준 {threshold:.0%} {sign})"
    return f"{word} ({value:.2f}초, 기준 {threshold:g}초 {sign})"


def build_state(signals: dict[str, Any]) -> dict[str, Any]:
    """진단 때 코드가 조회한 signals(metrics, logs, recent_change)를 기준과 비교한 글로 바꾼다."""
    metrics = signals.get("metrics") or {}
    logs = signals.get("logs") or {}
    recent = signals.get("recent_change")
    # p95가 기준과 같으면 회복 기준(미만이어야 정상)과 맞춰 느림으로 본다
    return {
        "5xx 에러율": _level(metrics.get("error_rate"), ERROR_RATE_HIGH, "높음", "정상", "percent"),
        "p95 응답시간": _level(metrics.get("p95_seconds"), P95_SLOW_SECONDS, "느림", "정상", "seconds"),
        "로그 신호": {label: f"{logs[name]}건" if logs.get(name) else "없음"
                   for name, label in LOG_LABELS.items()},
        "이상 시작 직전 배포·설정 변경": "알 수 없음" if recent is None else "있음" if recent else "없음",
    }


def _new_client() -> Any:
    from typesafe_sdk import AsyncTypeSafeClient

    return AsyncTypeSafeClient(model=JEV_MODEL, timeout=JEV_TIMEOUT_SECONDS)


async def classify_fault(signals: dict[str, Any], *, client: Any = None,
                         timeout: float = JEV_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """장애 형태 조항과 확신도. 실패·시간 초과·키 없음이면 None이고 예외를 내지 않는다."""
    if client is None and not os.getenv("TYPESAFE_API_KEY"):
        return None
    try:
        own = client is None
        client = client or _new_client()
        try:
            response = await asyncio.wait_for(
                client.system_one(state=build_state(signals), questions={"fault": FAULT_QUESTION}),
                timeout)
        finally:
            if own:
                await client.aclose()
        answer = response.answers["fault"]
        return {"choice": answer.choice, "confidence": answer.confidence,
                "probabilities": dict(answer.probabilities)}
    except Exception as exc:  # 판정은 참고값이라 어떤 실패도 작업을 멈추지 않는다
        log.warning("Jev 판정 실패: %s", exc)
        return None

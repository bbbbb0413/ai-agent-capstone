"""코드가 정해진 식으로 조회하는 관측값: 로그 신호, 지표, 직전 변경, 회복 기준.

모델에게 조회를 맡겼을 때 로그를 보지 않거나 메트릭 이름을 지어낸 적이 있어(docs/harness-spec.md), 장애 형태를 가르는
값은 코드가 먼저 조회해 모델과 조치 검사(action_guards)에 넘긴다. 판단은 하지 않는다.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.toolbox import ToolBox, parse_json

_LOG_NUMBERS = re.compile(r"\d+")


# 장애 형태를 가르는 알려진 로그 신호 (공통 런북 RB-STD 조항의 증상). 코드는 있는지와 건수만 센다. 판단은 모델이 한다.
# 신호 이름은 런북 조항과 묶여 고정이고, 로그 문구는 서비스마다 서비스 카탈로그(observability.logSignals)에 있다
LOG_SIGNALS = {
    "connection_pool_exhausted": "RB-STD-01",
    "worker_pool_saturated": "RB-STD-02",
    "request_queue_backlog": "RB-STD-03",
    "external_api_timeout": "RB-STD-04",
    "slow_query": "RB-STD-05",
}


def observability(context: dict[str, Any], service: str | None) -> dict[str, Any] | None:
    """서비스 목록(inventory)에서 그 서비스의 관측 정의. 서비스를 모르거나 정의가 없으면 None.

    쿼리는 이 값과 service 조건으로만 만든다. 정의가 없을 때 데모 앱 기본값으로 대신하면 다른 서비스의 지표로
    진단하게 된다 (SPEC-limitations-fixes 4단계).
    """
    services = context.get("services") if isinstance(context.get("services"), list) else []
    item = next((s for s in services if isinstance(s, dict) and s.get("name") == service), None) if service else None
    obs = (item or {}).get("observability")
    return obs if isinstance(obs, dict) and obs.get("requestMetric") and obs.get("logSelector") else None


def _matchers(*parts: str | None) -> str:
    return ", ".join(p for p in parts if p)


def metric_queries(obs: dict[str, Any], service: str, window: str) -> dict[str, str]:
    """에러율, p95, 초당 요청 수. 모든 쿼리를 그 서비스로 거른다. 고객사 구분은 고객사별 데이터소스가 한다 (harness)."""
    name, where = obs["requestMetric"], f'service="{service}"'
    return {
        "error_rate": f'(sum(rate({name}_count{{{_matchers(where, obs.get("errorSelector"))}}}[{window}])) or vector(0))'
                      f' / sum(rate({name}_count{{{where}}}[{window}]))',
        "p95_seconds": f'histogram_quantile(0.95, sum by (le) (rate({name}_bucket{{{where}}}[{window}])))',
        "requests_per_second": f'sum(rate({name}_count{{{where}}}[{window}]))',
    }


def log_query(obs: dict[str, Any], service: str) -> str:
    return f'{{{_matchers(obs["logSelector"], f"service=\"{service}\"", "level=~\"ERROR|WARN\"")}}}'
LOG_WINDOW = timedelta(minutes=10)
LOG_LEAD = timedelta(minutes=2)   # 알림(rate 30s + for 15s + 평가 주기)은 이상 시작보다 40초 남짓 늦게 인시던트를 만든다. 넉넉히 둔다


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def log_window_start(now: datetime, incident: dict[str, Any] | None) -> datetime:
    """최근 10분과 인시던트 시작 2분 전 중 늦은 시각. 같은 서비스에서 앞서 끝난 장애의 로그를 원인으로 읽지 않게 한다.

    E2E에서 B(파드 멈춤) 직후 B2(결제 지연)를 진단할 때, 10분 창에 남은 B의 `worker pool saturated` 로그를 보고
    재시작을 제안한 적이 있다 (docs/harness-spec.md).
    같은 서비스의 직전 인시던트가 그 사이에 닫혔으면(load_context가 넣는 previousResolvedAt) 그 뒤부터 본다.
    앞 장애 직후에 새 장애가 나도 앞 장애의 로그가 근거에 섞이지 않는다. E2E는 그래서 장애 사이에 쉬지 않는다
    (SPEC-e2e-speed ②).
    """
    start = now - LOG_WINDOW
    started = _time((incident or {}).get("startedAt"))
    if started is None:
        return start
    start = max(start, min(now - LOG_LEAD, started - LOG_LEAD))
    previous = _time((incident or {}).get("previousResolvedAt"))
    return max(start, previous) if previous and previous < started else start


async def recent_log_patterns(box: ToolBox, counts: dict[str, int], incident_id: int | None,
                              incident: dict[str, Any] | None, obs: dict[str, Any], service: str) -> tuple[str, dict]:
    """최근 ERROR·WARN 로그를 코드가 먼저 조회해 반복 메시지별로 센다. 구간은 log_window_start.

    모델이 로그를 조회하지 않거나 단어로 미리 걸러 원인 로그(결제 API timeout 등)를 놓친 적이 있다 (docs/harness-spec.md).
    숫자를 지운 메시지를 패턴으로 묶는다. 마스킹과 tool_output 경계는 ToolBox가 이미 적용했다.
    """
    now = datetime.now(timezone.utc)
    args = {"logql": log_query(obs, service), "limit": 200, "direction": "backward",
            "startRfc3339": log_window_start(now, incident).isoformat(timespec="seconds"),
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
    # 묶음 키는 숫자를 #으로 바꿨으므로 신호 문구도 같게 바꿔 찾는다. "503 worker pool saturated"가 0건으로 세진 적이 있다
    # 카탈로그에 문구가 없는 신호는 셀 수 없어 넣지 않는다 (0건과 구별한다)
    phrases = obs.get("logSignals") if isinstance(obs.get("logSignals"), dict) else {}
    signals = {name: sum(p["count"] for key, p in patterns.items() if _LOG_NUMBERS.sub("#", str(phrases[name])) in key)
               for name in LOG_SIGNALS if phrases.get(name)}
    return summary_text, {"tool": "grafana_query_loki_logs", "args": args, "status": result.status,
                          "summary": summary_text[:500], "signals": signals}


SIGNAL_STEP_SECONDS = 5


def signal_count_query(obs: dict[str, Any], service: str, phrase: str) -> str:
    """그 신호 문구가 든 ERROR·WARN 로그를 5초 구간마다 센다. 구간 합이 건수, 처음 0이 아닌 구간이 처음 나온 시각이다."""
    return (f"sum(count_over_time({log_query(obs, service)} |= {json.dumps(phrase, ensure_ascii=False)} "
            f"[{SIGNAL_STEP_SECONDS}s]))")


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


async def log_signal_details(box: ToolBox, obs: dict[str, Any], service: str, start: datetime,
                             end: datetime) -> dict[str, dict[str, Any]]:
    """알려진 로그 신호마다 구간 안의 건수와 처음 나온 시각 {신호: {"count", "first_seen"}}.

    반복 메시지 요약(recent_log_patterns)은 최근 200줄만 본다. 파드 멈춤처럼 초당 수십 줄 나오는 신호가 있으면 200줄이
    몇 초 분량이라 함께 난 다른 신호가 0건으로 세지고 처음 시각도 알 수 없다. 그래서 신호마다 Loki가 센 값을 받는다
    (SPEC-compound-faults 3장). 조회에 실패한 신호와 카탈로그에 문구가 없는 신호는 넣지 않는다(0건과 구별한다).
    정해진 조회라 모델의 탐색 한도에 넣지 않는다.
    """
    phrases = obs.get("logSignals") if isinstance(obs.get("logSignals"), dict) else {}
    details: dict[str, dict[str, Any]] = {}
    for name in LOG_SIGNALS:
        if not phrases.get(name):
            continue
        args = {"logql": signal_count_query(obs, service, str(phrases[name])), "queryType": "range",
                "stepSeconds": SIGNAL_STEP_SECONDS, "startRfc3339": start.isoformat(timespec="seconds"),
                "endRfc3339": end.isoformat(timespec="seconds")}
        # 10분이면 120구간이라 결과 길이 제한(4000자)을 넘는다. 모델에 바로 가지 않으므로 넉넉히 둔다
        result = await box.call("diagnose", "grafana_query_loki_logs", args, {}, max_chars=200_000)
        if result.status == "error":
            continue
        rows = parse_json(result)
        series = rows.get("data") if isinstance(rows, dict) else None
        if not isinstance(series, list):
            continue
        try:
            points = sorted((float(p["timestamp"]), float(p["value"]))
                            for s in series if isinstance(s, dict) for p in s.get("values") or [])
        except (KeyError, TypeError, ValueError):
            continue
        hits = [(t, v) for t, v in points if v > 0]
        details[name] = {"count": int(sum(v for _, v in hits)),
                         # 구간 끝 시각에 그 앞 5초를 센 값이 찍힌다. 처음 나온 시각은 구간 시작으로 적는다
                         "first_seen": _iso(hits[0][0] - SIGNAL_STEP_SECONDS) if hits else None}
    return details


async def dependency_health(box: ToolBox, context: dict[str, Any], service: str) -> list[dict[str, Any]]:
    """서비스 목록에 적힌 그 서비스의 의존 대상마다 상태를 코드가 미리 본다. 모델이 의존 대상 상태를 보지 않고
    병목을 정하지 않게 한다. 조회에 실패한 대상은 status UNKNOWN으로 남긴다."""
    services = context.get("services") if isinstance(context.get("services"), list) else []
    item = next((s for s in services if isinstance(s, dict) and s.get("name") == service), None) or {}
    found = []
    for dep in item.get("dependencies") or []:
        if not isinstance(dep, dict) or not dep.get("name"):
            continue
        result = await box.call("diagnose", "business_get_dependency_health", {"dependency": dep["name"]}, {})
        health = parse_json(result) if result.status != "error" else None
        health = health if isinstance(health, dict) else {}
        found.append({"name": dep["name"], "kind": dep.get("kind"), "owner": dep.get("owner"),
                      "status": health.get("status") or "UNKNOWN", "p95Seconds": health.get("p95Seconds")})
    return found


# 근거 조건 (SPEC-compound-faults 3.1). 순위가 아니라 "근거 없는 원인"을 가려내는 최소 기준이다
MIN_SIGNAL_COUNT = 3   # 이보다 적은 신호는 잡음으로 본다
DEPENDENCY_CLAUSES = {"database": "RB-STD-05", "external_api": "RB-STD-04"}
UNHEALTHY = {"DEGRADED", "DOWN"}


def cause_facts(log_signals: dict[str, dict[str, Any]], dependencies: list[dict[str, Any]],
                metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """원인 후보(공통 런북 RB-STD-01~05)별 사실 표. 코드가 조회한 값을 조항별로 모을 뿐 순위·흡수는 정하지 않는다.
    판단은 모델이 한다 (prompts/diagnose.md). evidence는 조치 검사(action_guards)의 "근거 없는 원인" 기준이다.

    dependency_share는 의존 대상 p95를 요청 p95로 나눈 값이다. 의존 호출이 처리 시간을 얼마나 차지하는지 본다.
    """
    p95 = metrics.get("p95_seconds")
    facts = []
    for name, clause in LOG_SIGNALS.items():
        signal = log_signals.get(name)
        deps = [d for d in dependencies if DEPENDENCY_CLAUSES.get(str(d.get("kind"))) == clause]
        if signal is None and not deps:
            continue   # 셀 수 없는 신호이고 해당 의존 대상도 없으면 사실이 없다
        count = (signal or {}).get("count")
        dep = max(deps, key=lambda d: d.get("p95Seconds") or 0, default=None)
        dep_p95 = (dep or {}).get("p95Seconds")
        share = (round(dep_p95 / p95, 2) if isinstance(dep_p95, (int, float)) and isinstance(p95, (int, float))
                 and p95 > 0 else None)
        facts.append({
            "clause": clause, "signal": name, "signal_count": count, "first_seen": (signal or {}).get("first_seen"),
            "dependency": (dep or {}).get("name"), "dependency_status": (dep or {}).get("status"),
            "dependency_share": share,
            "evidence": (count or 0) >= MIN_SIGNAL_COUNT or any(d.get("status") in UNHEALTHY for d in deps),
        })
    return facts


def diagnose_queries(obs: dict[str, Any], service: str) -> dict[str, str]:
    """진단 시작 때 코드가 조회하는 기본 지표 (최근 1분). 에러율은 RB-STD-02(5xx 급증)와 RB-STD-03(에러 없이 지연)을
    가르는 기준인데, 모델이 에러율을 조회하지 않고 대기열 경고만 보고 RB-STD-02(재시작)로 판단한 적이 있다 (docs/harness-spec.md)
    """
    return metric_queries(obs, service, "1m")


async def observe(box: ToolBox, log_counts: dict[str, int], incident_id: int | None,
                  incident: dict[str, Any] | None, obs: dict[str, Any],
                  service: str) -> tuple[str, dict, dict[str, float | None]]:
    """진단 때 코드가 미리 보는 그 서비스의 로그 반복 메시지와 지표. (로그 요약, 로그 근거, 지표)

    지표는 정해진 쿼리라 모델의 탐색 한도(같은 도구 3번)에 넣지 않는다. 넣으면 모델이 Prometheus를 더 부를 수 없다.
    로그 조회를 어느 한도에 넣을지는 호출한 쪽이 log_counts로 정한다.
    """
    logs, logged = await recent_log_patterns(box, log_counts, incident_id, incident, obs, service)
    metric_counts: dict[str, int] = {}
    metrics = {name: await metric(box, expr, metric_counts, node="diagnose")
               for name, expr in diagnose_queries(obs, service).items()}
    return logs, logged, metrics


RECENT_CHANGE_MINUTES = 15   # 이상 시작 전 이 시간 안의 배포·설정 변경을 "직전 변경"으로 본다 (RB-STD-01)


def recent_change(changes: Any, started_at: str | None) -> bool | None:
    """인시던트 시작 전 RECENT_CHANGE_MINUTES 안에 배포·설정 변경이 있었나. 판단할 수 없으면 None."""
    if not isinstance(changes, dict) or not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        # 배포 이력은 deployedAt(실제로 배포된 시각, 현재 리비전만)을 먼저 본다. createdAt은 예전 설정으로 다시
        # 배포하면 옛 ReplicaSet의 생성 시각이 남아 직전 변경을 놓친다 (b-pool E2E)
        times = [datetime.fromisoformat(str(c.get(key)).replace("Z", "+00:00"))
                 for c in (changes.get("revisions") or changes.get("commits") or []) if isinstance(c, dict)
                 for key in ("deployedAt", "createdAt", "date") if c.get(key)]
    except ValueError:
        return None
    return any(start - timedelta(minutes=RECENT_CHANGE_MINUTES) <= t <= start for t in times)


VERIFY_WINDOW = "15s"


def verify_queries(obs: dict[str, Any], service: str) -> dict[str, str]:
    """회복 확인 쿼리. 알림 규칙(infra/prometheus/alert-rules.yml)과 같은 기준을 조치한 서비스만, 조치 직후 15초 창으로 본다.

    에러가 하나도 없으면 에러 시계열이 없으므로 `or vector(0)`을 붙인다. 요청 자체가 없으면 결과가 비어 None이 된다.
    서비스 조건이 없을 때는 같은 고객사의 다른 서비스 지표가 섞였다.
    """
    queries = metric_queries(obs, service, VERIFY_WINDOW)
    return {"error_rate": queries["error_rate"], "p95_seconds": queries["p95_seconds"]}
ERROR_RATE_THRESHOLD = 0.2
P95_THRESHOLD_SECONDS = 1.0


async def metric(box: ToolBox, expr: str, counts: dict[str, int], node: str = "verify") -> float | None:
    result = parse_json(await box.call(node, "grafana_query_prometheus",
                                       {"expr": expr, "queryType": "instant", "endTime": "now"}, counts))
    try:
        return float(result["data"][0]["value"][1])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def within_thresholds(sample: dict[str, Any]) -> bool:
    """알림 기준 안이다. 에러율이 없으면(요청 메트릭 미수집) 기준 안으로 보지 않고, p95가 없으면 지연은 보지 않는다."""
    error_rate = sample.get("error_rate")
    return (error_rate is not None and error_rate < ERROR_RATE_THRESHOLD
            and (sample.get("p95_seconds") or 0) < P95_THRESHOLD_SECONDS)

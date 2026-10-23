"""모델이 고른 조치를 승인 요청으로 올리기 전에 코드가 검사한다 (propose_action의 되돌려 보내기).

판단은 모델이 한다. 여기서는 공통 런북 조항·서비스 목록·코드가 센 신호와 어긋나는 선택만 이유와 함께 돌려준다.
각 검사는 E2E에서 실제로 나온 잘못된 선택에서 왔다 (docs/harness-spec.md).
"""

import re

from langchain_core.messages import AIMessage

# 공통 런북 조항별로 고칠 수 있는 조치 (data/documents/_common/runbook-standard.md). 에스컬레이션·공지는 언제나 된다.
# 할 수 없으면 다른 조치로 대신하지 않는다: B사 급증에서 확장이 계약상 불가하자 효과 없는 재시작을 제안한 적이 있다
# tests/test_runbook_clauses.py가 문서와 같은지 본다
CLAUSE_ACTIONS: dict[str, set[str]] = {
    "RB-STD-01": {"rollback_deployment", "restart_pods"},   # 직전 변경이 없으면 RB-STD-02의 재시작
    "RB-STD-02": {"restart_pods"},
    "RB-STD-03": {"scale_deployment", "adjust_autoscaler"},
    "RB-STD-04": set(), "RB-STD-05": set(), "RB-STD-06": set(), "RB-STD-07": set(),
}
_CLAUSE = re.compile(r"RB-STD-0\d")


def fixes(clause: str, recent_change: bool) -> set[str]:
    """그 장애 형태를 고치는 조치. RB-STD-01의 재시작은 직전 변경이 없을 때만이다.

    직전 변경이 있는데 롤백이 계약상 불가하자 재시작으로 대신한 적이 있다 (B사 풀 고갈).
    """
    return CLAUSE_ACTIONS[clause] - ({"restart_pods"} if clause == "RB-STD-01" and recent_change else set())


def first_problem(ai: AIMessage, context: dict, signals: dict, recent_change: bool, target: str | None = None,
                  handled: list[dict] | tuple = ()) -> str | None:
    """돌려보낼 이유. 맞지 않는 점이 없으면 None.

    target은 진단이 적은 처리할 원인(graph.parse_causes), handled는 이 작업에서 이미 조치한 원인이다.
    """
    return (target_without_evidence(target, signals) or target_already_handled(ai, target, handled)
            or target_mismatch(ai, target, recent_change)
            or clause_mismatch(ai, recent_change) or unsupported_action(ai, context)
            or blocked_action_mismatch(ai, signals, recent_change, target) or blocked_scaling_unfit(ai, context))


# 겹친 장애의 처리할 원인 검사 (SPEC-compound-faults 4.2). 원인 후보의 순위는 모델이 정하고, 여기서는 명백한 위반만 막는다

def target_without_evidence(target: str | None, signals: dict) -> str | None:
    """① 처리할 원인에 근거가 없다: 코드가 정리한 사실 표에서 그 조항의 evidence가 false다. 사실 표에 없는 조항은 보지 않는다."""
    fact = next((f for f in signals.get("facts") or [] if f.get("clause") == target), None) if target else None
    if not fact or fact.get("evidence"):
        return None
    return (f"거부: 처리할 원인 {target}에는 근거가 없다 (신호 {fact.get('signal')} {fact.get('signal_count')}건, "
            f"의존 대상 상태 {fact.get('dependency_status') or '없음'}). 근거가 있는 원인 후보를 처리하거나, "
            "근거가 있는 후보가 없으면 원인 미확정(RB-STD-07)으로 에스컬레이션한다.")


def target_already_handled(ai: AIMessage, target: str | None, handled: list[dict] | tuple) -> str | None:
    """② 이 작업에서 이미 앱 조치를 한 원인에 앱 조치를 또 하려 한다. 효과가 없던 원인에 다른 조치를 시도하지 않는다.

    같은 원인의 에스컬레이션은 된다: 최대치를 올려도 회복되지 않은 트래픽 급증은 구조상 더 늘릴 수 없다는
    ACTION_BLOCKED 에스컬레이션으로 넘긴다 (a-surge-max).
    """
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_execute_runbook_action":
        return None
    done = next((h for h in handled if h.get("clause") == target), None) if target else None
    if not done:
        return None
    return (f"거부: {target}은 이미 {done.get('action')}로 조치했고 회복되지 않았다. 같은 원인에 다른 앱 조치를 시도하지 "
            "않는다. 그 원인의 신호가 사라졌으면 처리하지 않은 다음 원인 후보를 처리하고, 남아 있으면 "
            "에스컬레이션한다.")


def target_mismatch(ai: AIMessage, target: str | None, recent_change: bool) -> str | None:
    """③ 고른 조치가 처리할 원인을 고치는 조치도, 그 조항에 맞는 에스컬레이션·공지도 아니다.

    앱 원인(RB-STD-01~03)은 그 조항의 조치이거나 막혀서 하는 ACTION_BLOCKED 에스컬레이션이다. 외부 원인(RB-STD-04)은
    고객 공지, DB 원인(RB-STD-05)은 DEPENDENCY 에스컬레이션이다. P1·원인 미확정 에스컬레이션은 언제나 된다.
    """
    if not target or target not in CLAUSE_ACTIONS or not ai.tool_calls:
        return None
    call = ai.tool_calls[0]
    name, args = call["name"], call["args"]
    fits = fixes(target, recent_change)
    if name == "business_execute_runbook_action":
        ok = args.get("action") in fits
    elif name == "business_send_customer_notice":
        ok = target == "RB-STD-04"
    elif name == "business_escalate_incident":
        category = args.get("category")
        ok = (category not in ("ACTION_BLOCKED", "DEPENDENCY")
              or (category == "ACTION_BLOCKED" and bool(fits))
              or (category == "DEPENDENCY" and target in ("RB-STD-04", "RB-STD-05")))
    else:
        ok = True
    if ok:
        return None
    expected = {"RB-STD-04": "고객 공지(business_send_customer_notice)",
                "RB-STD-05": "DEPENDENCY 에스컬레이션"}.get(
        target, f"{sorted(fits)} 중 하나, 할 수 없으면 ACTION_BLOCKED 에스컬레이션")
    return (f"거부: 진단이 정한 처리할 원인은 {target}이고 그 조치는 {expected}다. 고른 조치({name} "
            f"{args.get('action') or args.get('category') or ''})는 다른 원인의 조치다. 처리할 원인 하나만 조치한다.")


def unsupported_action(ai: AIMessage, context: dict) -> str | None:
    """고른 조치가 그 서비스의 supportedActions(계약과 실행 환경이 모두 허용하는 조치)에 없으면 그 이유.

    backend도 422로 막지만, 실행할 수 없는 조치가 사람의 승인 요청으로 올라가지 않게 제안 단계에서 돌려보낸다.
    """
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_execute_runbook_action":
        return None
    args = ai.tool_calls[0]["args"]
    services = context.get("services") if isinstance(context.get("services"), list) else []
    item = next((s for s in services if isinstance(s, dict) and s.get("name") == args.get("service")), None)
    supported = (item or {}).get("supportedActions")
    if not isinstance(supported, list) or args.get("action") in supported:
        return None
    return (f"거부: {args.get('service')}의 supportedActions는 {supported}다. '{args.get('action')}'는 계약이나 실행 환경상 "
            "할 수 없다. 다른 조치로 대신하지 말고 business_escalate_incident(category=\"ACTION_BLOCKED\", "
            f"required_action=\"{args.get('action')}\", blocked_by=\"CONTRACT\" 또는 \"RUNTIME\")를 호출한다 (RB-STD-06).")


def signal_clause(signals: dict) -> str | None:
    """코드가 센 지표와 로그 신호로 정한 장애 형태 (prompts/diagnose.md의 판정표 앞 세 행). 애매하면 None.
    진단이 처리할 원인을 적었으면 그것을 쓴다 (blocked_action_mismatch)."""
    error_rate = (signals.get("metrics") or {}).get("error_rate")
    logs = signals.get("logs") or {}
    if not isinstance(error_rate, (int, float)):
        return None
    found = [clause for clause, hit in (
        ("RB-STD-01", error_rate >= 0.05 and logs.get("connection_pool_exhausted", 0) > 0),
        ("RB-STD-02", error_rate >= 0.05 and logs.get("worker_pool_saturated", 0) > 0),
        ("RB-STD-03", error_rate < 0.05 and logs.get("request_queue_backlog", 0) > 0
         and not logs.get("external_api_timeout") and not logs.get("slow_query")),
    ) if hit]
    return found[0] if len(found) == 1 else None


def blocked_action_mismatch(ai: AIMessage, signals: dict, recent_change: bool,
                            target: str | None = None) -> str | None:
    """ACTION_BLOCKED 에스컬레이션의 required_action이 신호로 정한 장애 형태의 조치와 다르면 그 이유.

    받는 사람(고객사 승인 담당)에게 엉뚱한 조치의 승인을 요청하게 된다. 풀 고갈(RB-STD-01)인데
    required_action을 scale_deployment로 적은 적이 있다 (b-pool E2E).
    """
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_escalate_incident":
        return None
    args = ai.tool_calls[0]["args"]
    # 신호가 둘 이상 맞으면 코드로는 하나를 정할 수 없다. 진단이 처리할 원인을 적었으면 그 조항으로 본다
    clause = target if target in CLAUSE_ACTIONS else signal_clause(signals)
    if args.get("category") != "ACTION_BLOCKED" or not args.get("required_action") or not clause:
        return None
    fits = fixes(clause, recent_change)
    if args["required_action"] in fits:
        return None
    return (f"거부: 지표와 로그 신호로는 {clause}다. 그 장애 형태를 고치는 조치는 {sorted(fits)}이고, "
            f"'{args['required_action']}'는 아니다. required_action에는 그중 할 수 없어 막힌 조치를 적는다.")


def blocked_scaling_unfit(ai: AIMessage, context: dict) -> str | None:
    """ACTION_BLOCKED 에스컬레이션의 확장 방식이 서비스 구조와 맞지 않으면 그 이유.

    HPA가 없으면 막힌 조치는 레플리카 확장(scale_deployment)이고, HPA가 있으면 최대치 조정(adjust_autoscaler)이다.
    B사(HPA 없음, 확장 계약상 불가) 급증에서 adjust_autoscaler·STRUCTURE로 적어 고객사 승인 담당 대신 엉뚱한 곳에
    에스컬레이션한 적이 있다 (b-surge E2E, 2026-10-23).
    """
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_escalate_incident":
        return None
    args = ai.tool_calls[0]["args"]
    required = args.get("required_action")
    if args.get("category") != "ACTION_BLOCKED" or required not in ("scale_deployment", "adjust_autoscaler"):
        return None
    services = context.get("services") if isinstance(context.get("services"), list) else []
    item = next((s for s in services if isinstance(s, dict) and s.get("name") == args.get("service")), None)
    if item is None:
        return None
    autoscaled = isinstance(item.get("autoscaling"), dict)
    fits = "adjust_autoscaler" if autoscaled else "scale_deployment"
    if required == fits:
        return None
    structure = "HPA가 레플리카를 관리한다" if autoscaled else "HPA가 없다"
    return (f"거부: {args.get('service')}에는 {structure}. 막힌 확장 조치는 '{fits}'이고 '{required}'가 아니다. "
            f"required_action=\"{fits}\"로 다시 적는다. 그 조치가 supportedActions({item.get('supportedActions')})에 "
            "없으면 blocked_by=\"CONTRACT\", 안전 상한에 막히면 \"STRUCTURE\"다.")


def clause_mismatch(ai: AIMessage, recent_change: bool = False) -> str | None:
    """제안 이유가 인용한 공통 런북 조항과 고른 조치가 맞지 않으면 그 이유. 조항을 인용하지 않았으면 검사하지 않는다."""
    if not ai.tool_calls or ai.tool_calls[0]["name"] != "business_execute_runbook_action":
        return None
    args = ai.tool_calls[0]["args"]
    clauses = sorted(set(_CLAUSE.findall(f"{args.get('reason', '')} {ai.content}")) & CLAUSE_ACTIONS.keys())
    fits = {c: fixes(c, recent_change) for c in clauses}
    if not clauses or any(args.get("action") in fits[c] for c in clauses):
        return None
    allowed = sorted({a for c in clauses for a in fits[c]})
    return (f"거부: {', '.join(clauses)}의 조치는 {allowed or '앱 조치 없음'}이다. '{args.get('action')}'는 이 장애 형태를 "
            "고치지 못한다. 그 조치가 supportedActions에 없거나 상한에 막히면 다른 조치로 대신하지 말고 "
            "business_escalate_incident(category=\"ACTION_BLOCKED\", required_action=…, blocked_by=…)를 호출한다. "
            "외부·DB 원인이면 공지 또는 DEPENDENCY 에스컬레이션이다.")

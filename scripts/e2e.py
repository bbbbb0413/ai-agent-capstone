#!/usr/bin/env python3
"""OpsLens 전체 시나리오 E2E 테스트. 장애 주입부터 승인, 조치, 회복 확인, 보고서까지 실제로 돌리고 결과를 판정한다.

    python3 scripts/e2e.py                # 전체: 사전 점검 → 격리 → 문서 질문 → B → B2 → A → A-hang
    python3 scripts/e2e.py b a-hang       # 고른 시나리오만 (사전 점검은 항상)
    python3 scripts/e2e.py --list         # 시나리오 목록

옵션
    --no-merge       시나리오 A의 롤백 PR을 스크립트가 병합하지 않는다. 사람이 GitHub에서 병합할 때까지 기다린다
    --auto-approve   backend 자동 승인 설정을 그대로 쓴다. 기본은 실행 동안 끄고, 스크립트가 제안을 검사한 뒤 승인한다
    --timeout 초     시나리오 하나의 최대 시간 (기본 600)

승인 정책: 기대한 조치면 승인하고, 아니면 거절하고 실패로 기록한다. 잘못된 조치가 실행되지 않게 하기 위해서다.
결과는 화면 표와 .local/e2e/<시각>.md 에 남는다. 비밀값은 출력하지 않는다(컨테이너 환경변수만 쓴다).
"""

import argparse
import hashlib
import os
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8080"
WEB = "http://localhost"
KCTX = "kind-opslens-tenants"
REPO = "bbbbb0413/ai-agent-capstone"
NODE_PORT = {"tenant-a": 30081, "tenant-b": 30082}
ADMIN_ONLY = {"business_send_customer_notice", "business_confirm_sla_credit"}
USERS = {"engineer": "engineer1234", "admin": "admin1234", "viewer-a": "viewer1234", "viewer-b": "viewer1234"}


# ── 출력 ──────────────────────────────────────────────────────

def say(msg: str) -> None:
    print(f"  {datetime.now():%H:%M:%S}  {msg}", flush=True)


@dataclass
class Result:
    name: str
    checks: list[tuple[bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    task_id: str | None = None
    seconds: float = 0.0
    report: str = ""

    def check(self, ok: bool, label: str) -> bool:
        self.checks.append((bool(ok), label))
        say(("✅ " if ok else "❌ ") + label)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(ok for ok, _ in self.checks)


# ── 호출 도구 ──────────────────────────────────────────────────

def sh(*cmd: str, check: bool = True, quiet: bool = False) -> str:
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}…: {out.stderr.strip()[:300]}")
    return out.stdout.strip() if not quiet else ""


def compose(*args: str, check: bool = True) -> str:
    return sh("docker", "compose", *args, check=check)


def in_backend(script: str) -> str:
    """backend 컨테이너 안에서 셸을 실행한다. SERVICE_TOKEN·GITHUB_TOKEN은 컨테이너 환경변수로만 쓴다."""
    return compose("exec", "-T", "backend", "sh", "-c", script)


def sql(query: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "opslens", "-tAc", query)


def kubectl(*args: str, check: bool = True) -> str:
    return sh("kubectl", "--context", KCTX, *args, check=check)


class Session:
    """화면과 같은 공개 API를 같은 쿠키 방식으로 호출한다."""

    def __init__(self, user: str) -> None:
        self.user = user
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, _ = self.call("POST", "/api/auth/login", {"username": user, "password": USERS[user]})
        if status != 200:
            raise RuntimeError(f"{user} 로그인 실패 ({status})")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")

    def get(self, path: str) -> object:
        status, body = self.call("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} → {status}")
        return body


# Agent는 인시던트 시작 2분 전부터 로그를 본다 (agent graph._log_window_start). 인시던트는 주입 약 70초 뒤에
# 생기므로, 앞 장애를 정리하고 이만큼 지난 뒤 주입해야 앞 장애의 로그가 다음 진단 근거에 섞이지 않는다
QUIET_SECONDS = 75
_cleared_at: dict[str, float] = {}


def inject(tenant: str, fault: str | None) -> None:
    """고객사 서비스에 장애를 주입한다. fault=None이면 해제한다."""
    if fault and (wait := QUIET_SECONDS - (time.time() - _cleared_at.get(tenant, 0))) > 0:
        say(f"앞 장애의 로그가 진단 구간에 섞이지 않게 {wait:.0f}초 기다린다")
        time.sleep(wait)
    url = f"http://opslens-tenants-control-plane:{NODE_PORT[tenant]}/admin/faults"
    method = f"-X POST -H 'Content-Type: application/json' -d '{{\"fault\":\"{fault}\"}}'" if fault else "-X DELETE"
    in_backend(f'curl -sf {method} -H "Authorization: Bearer $SERVICE_TOKEN" {url} > /dev/null')
    if not fault:
        _cleared_at[tenant] = time.time()


def github(method: str, path: str, body: dict | None = None) -> dict:
    data = f"-d '{json.dumps(body)}'" if body else ""
    out = in_backend(f'curl -s -X {method} -H "Authorization: Bearer $GITHUB_TOKEN" '
                     f'-H "Accept: application/vnd.github+json" {data} https://api.github.com/repos/{REPO}{path}')
    return json.loads(out or "{}")


# ── 작업 추적 ──────────────────────────────────────────────────

@dataclass
class Expect:
    """시나리오가 기대하는 Agent의 행동."""
    action_tool: str                       # 승인할 조치 도구
    action: str | None = None              # execute_runbook_action의 action
    service: str | None = None
    allow: set[str] = field(default_factory=lambda: {"business_escalate_incident"})


def wait_for_incident_task(admin: Session, tenant: str, since: datetime, timeout: float) -> tuple[dict, dict]:
    say(f"{tenant} 알림 → 인시던트·작업 생성을 기다린다 (알림 규칙 for 30s + 평가 주기)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for inc in admin.get(f"/api/incidents?status=OPEN"):
            if inc["tenantId"] == tenant and datetime.fromisoformat(inc["startedAt"]) >= since:
                for task in admin.get("/api/ai/tasks"):
                    if task.get("incidentId") == inc["id"]:
                        say(f"인시던트 #{inc['id']} ({inc['severity']}) · 작업 {task['id'][:8]} · {WEB}/tasks/{task['id']}")
                        return inc, task
        time.sleep(5)
    raise TimeoutError(f"{timeout:.0f}초 안에 {tenant} 인시던트가 생기지 않았다")


def drive(task_id: str, expect: Expect, result: Result, timeout: float, merge: bool) -> dict:
    """작업을 끝까지 진행한다. 승인 대기면 제안을 검사해 승인·거절하고, PR 대기면 병합한다."""
    engineer, admin = Session("engineer"), Session("admin")
    seen: set[str] = set()
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        task = engineer.get(f"/api/ai/tasks/{task_id}")
        status, pending = task["status"], task.get("pendingAction") or {}
        if status != last:
            say(f"상태: {status}")
            last = status
        if status in ("DONE", "FAILED"):
            return task
        key = pending.get("approvalId") or json.dumps(pending, sort_keys=True)
        if status == "WAITING_APPROVAL" and pending and key not in seen:
            seen.add(key)
            approve(task_id, pending, expect, result, admin if pending.get("tool") in ADMIN_ONLY else engineer)
        elif status == "CHANGE_PENDING" and pending and key not in seen:
            seen.add(key)
            handle_pr(pending, result, merge)
        time.sleep(4)
    raise TimeoutError(f"{timeout:.0f}초 안에 작업이 끝나지 않았다 (마지막 상태 {last})")


def expected(pending: dict, expect: Expect) -> bool:
    tool, args = pending.get("tool"), pending.get("args") or {}
    if tool == expect.action_tool:
        return all(args.get(k) == v for k, v in (("action", expect.action), ("service", expect.service)) if v)
    return tool in expect.allow or tool == "business_send_customer_notice"


def describe(pending: dict) -> str:
    args = pending.get("args") or {}
    return " ".join(str(x) for x in (pending.get("tool"), args.get("action"), args.get("service")) if x)


def approve(task_id: str, pending: dict, expect: Expect, result: Result, who: Session) -> None:
    """기대한 제안만 승인한다. 판정(✅/❌)은 끝난 뒤 check_proposals가 저장된 이벤트로 한다."""
    ok = expected(pending, expect)
    say(f"승인 요청: {describe(pending)}")
    decision = "APPROVE" if ok else "REJECT"
    status, body = who.call("POST", f"/api/ai/tasks/{task_id}/approval",
                            {"decision": decision, "comment": "e2e", "approvalId": pending.get("approvalId")})
    if status == 202:
        say(f"→ {who.user}가 {'승인' if ok else '거절'}")
    elif status == 409:
        say("→ 이미 처리됨 (자동 승인)")
    else:
        result.check(False, f"승인 API 응답 {status}: {str(body)[:150]}")


def handle_pr(pending: dict, result: Result, merge: bool) -> None:
    number, url = pending.get("prNumber"), pending.get("prUrl")
    files = pending.get("files") or []
    result.check(bool(number) and all(f.startswith("gitops/tenants/tenant-a/") for f in files),
                 f"PR #{number}이 A사 설정 경로만 바꾼다 ({', '.join(files)})")
    result.notes.append(f"롤백 PR: {url}")
    if not merge:
        say(f"→ PR을 병합해 주세요: {url} (병합되면 자동으로 이어진다)")
        return
    out = github("PUT", f"/pulls/{number}/merge", {"merge_method": "merge"})
    result.check(out.get("merged") is True, f"PR #{number} 병합 ({out.get('message', '')})")


def check_proposals(task_id: str, expect: Expect, result: Result) -> dict | None:
    """저장된 이벤트로 Agent가 낸 승인 요청을 검사한다. 자동 승인으로 진행돼도 같은 기준으로 판정한다."""
    events = Session("engineer").get(f"/api/ai/tasks/{task_id}/events")
    proposals = [e["data"] for e in events if e["type"] == "APPROVAL_REQUIRED" and e.get("data")]
    unique = list({str(p.get("tool")) + json.dumps(p.get("args"), sort_keys=True): p for p in proposals}.values())
    actions = [p for p in unique if p.get("tool") == expect.action_tool]
    matching = next((p for p in actions if expected(p, expect)), None)
    result.check(bool(matching),
                 f"기대한 조치를 제안했다 ({', '.join(describe(p) for p in actions) or '조치 제안 없음'})")
    result.check(len(actions) <= 1, f"조치 제안이 한 번이다 ({len(actions)}번. 두 번 이상이면 회복 판단을 의심한다)")
    wrong = [describe(p) for p in unique if not expected(p, expect)]
    result.check(not wrong, "기대하지 않은 승인 요청이 없다" + (f" (있음: {', '.join(wrong)})" if wrong else ""))
    return matching


def summarize(task: dict, result: Result) -> dict:
    summary = task.get("summary") or {}
    result.task_id = task["id"]
    result.report = (task.get("answer") or "")[:1500]
    result.check(task["status"] == "DONE", f"작업 완료 (상태 {task['status']})")
    return summary


# ── 시나리오 ───────────────────────────────────────────────────

def preflight(args) -> Result:
    r = Result("사전 점검")
    services = compose("ps", "--format", "{{.Service}} {{.State}}", check=False)
    need = {"backend", "agent", "business-mcp", "rag-mcp", "mcp-grafana", "postgres", "prometheus",
            "alertmanager", "loki", "frontend"}
    running = {line.split()[0] for line in services.splitlines() if line.endswith("running")}
    stack_ready = need <= running
    r.check(stack_ready, f"compose 서비스 실행 중 ({len(need & running)}/{len(need)})"
            + (f", 없음: {', '.join(sorted(need - running))}" if need - running else ""))
    ready = kubectl("get", "deploy", "-A", "-l", "app", "-o",
                    "jsonpath={range .items[*]}{.metadata.name}={.status.readyReplicas}/{.spec.replicas} {end}",
                    check=False)
    r.check("order-api" in ready and "reservation-api" in ready, f"kind 고객사 서비스 ({ready.strip() or '응답 없음'})")
    sync = kubectl("-n", "argocd", "get", "application", "tenant-a", "-o",
                   "jsonpath={.status.sync.status}/{.status.health.status}", check=False)
    r.check(sync == "Synced/Healthy", f"ArgoCD tenant-a {sync or '없음'}")
    if "postgres" in running:
        common = ROOT / "data/documents/_common/runbook-standard.md"
        version = hashlib.sha256(common.read_bytes()).hexdigest()[:12]
        indexed = sql("select count(*) from docs.chunks where tenant_id = '_common' "
                      f"and doc_id = 'runbook-standard' and version = '{version}'")
        r.check(indexed not in ("", "0"), "현재 버전의 공통 표준 런북이 색인됐다 (RAG)")
    else:
        r.check(False, "현재 버전의 공통 표준 런북이 색인됐다 (PostgreSQL 미실행)")
    if "a" in args.scenarios:
        token = in_backend('[ -n "$GITHUB_TOKEN" ] && echo yes || echo no') if "backend" in running else "no"
        r.check(token == "yes", "backend에 GITHUB_TOKEN이 있다 (시나리오 A의 PR)")
    if "postgres" in running:
        busy = sql("select string_agg(left(id::text, 8) || ' ' || tenant_id || ' ' || status || ' (' || "
                   "to_char(updated_at at time zone 'Asia/Seoul', 'HH24:MI') || ')', ', ') "
                   "from app.ai_tasks where status not in ('DONE','FAILED')")
        if not r.check(not busy, "진행 중인 작업이 없다" + (f" (있음: {busy})" if busy else "")):
            r.notes.append("진행 중인 작업은 화면에서 끝까지 처리한다. 체크포인트가 없는 중단 작업은 FAILED로 정리한다")
        open_incidents = sql("select string_agg('#'||id||' '||tenant_id||' '||service, ', ') from app.incidents "
                             "where status = 'OPEN' and tenant_id in ('tenant-a','tenant-b')")
        if not r.check(not open_incidents,
                       "열린 인시던트가 없다" + (f" (있음: {open_incidents})" if open_incidents else "")):
            r.notes.append(f"열린 인시던트가 있다: {open_incidents}. 같은 알림이면 새 인시던트가 생기지 않는다")
    return r


def scenario_isolation(args) -> Result:
    r = Result("D. 고객사 격리")
    viewer_b = Session("viewer-b")
    a_incident = sql("select id from app.incidents where tenant_id = 'tenant-a' order by id desc limit 1")
    if a_incident:
        status, _ = viewer_b.call("GET", f"/api/incidents/{a_incident}")
        r.check(status in (403, 404), f"B사 담당자가 A사 인시던트 조회 → {status}")
    status, _ = viewer_b.call("POST", "/api/ai/tasks", {"tenantId": "tenant-a", "question": "A사 상태 알려줘"})
    r.check(status in (401, 403), f"B사 담당자가 A사 작업 생성 → {status}")
    code = in_backend('curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $SERVICE_TOKEN" '
                      '-H "X-Tenant-Id: tenant-b" http://localhost:8080/internal/tenants/tenant-a/services')
    r.check(code == "403", f"B사 작업 컨텍스트로 A사 내부 API 호출 → {code}")
    # backend가 쓰는 B사 ServiceAccount 토큰 그대로. kubeconfig의 관리자 인증서가 섞이지 않게 빈 설정으로 호출한다
    server = kubectl("config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    can = subprocess.run(["kubectl", "--kubeconfig", "/dev/null", "--server", server,
                          "--certificate-authority", str(ROOT / ".local/tenants/ca.crt"),
                          "--token", (ROOT / ".local/tenants/tenant-b.token").read_text().strip(),
                          "auth", "can-i", "delete", "pods", "-n", "tenant-a"], capture_output=True, text=True)
    answer = can.stdout.strip() or can.stderr.strip()[:80]
    r.check(answer == "no", f"B사 ServiceAccount로 A사 파드 삭제 권한 → {answer}")
    # RAG: B사 작업 컨텍스트로 A사에만 있는 내용을 검색해도 A사 문서는 나오지 않고, 표준 런북은 나온다
    probe = compose("exec", "-T", "agent", "/app/.venv/bin/python", "-c", """
import asyncio, json
from agent.toolbox import load_tools, _as_text
async def main():
    # Harness의 결과 길이 제한(4000자)을 거치지 않게 MCP 도구를 직접 부른다. 고객사는 연결 헤더로만 정해진다
    tools = {t.name: t for t in await load_tools("tenant-b", "e2e", "e2e")}
    out = await tools["rag_search_documents"].ainvoke({"query": "A사 DBA 연락, GitOps PR 병합, order-api", "top_k": 10})
    print(json.dumps([d["uri"] for d in json.loads(_as_text(out))]))
asyncio.run(main())
""")
    uris = json.loads(probe.splitlines()[-1]) if probe else []
    r.check(uris and not any("tenant-a" in u for u in uris),
            f"B사 작업의 문서 검색에 A사 문서가 없다 ({len(uris)}건 중 A사 {sum('tenant-a' in u for u in uris)}건)")
    r.check(any("_common" in u for u in uris), "B사 작업에서도 MSP 표준 런북은 검색된다")
    blocked = sql("select count(*) from app.audit_logs where action = 'TENANT_ISOLATION_BLOCKED' "
                  "and created_at > now() - interval '2 minutes'")
    r.check(blocked != "0", f"격리 차단이 감사 로그에 남았다 ({blocked}건)")
    return r


def internal_action(tenant: str, body: dict) -> tuple[int, str]:
    """business-mcp가 부르는 backend 내부 조치 API를 직접 부른다. Agent·승인을 거치지 않는 방어선 검사용."""
    payload = json.dumps(body).replace("'", "")
    out = in_backend(f'curl -s -w "\\n%{{http_code}}" -H "Authorization: Bearer $SERVICE_TOKEN" '
                     f'-H "X-Tenant-Id: {tenant}" -H "Content-Type: application/json" '
                     f"-d '{payload}' http://localhost:8080/internal/tenants/{tenant}/actions")
    text, _, code = out.rpartition("\n")
    try:
        message = json.loads(text).get("message") or text
    except ValueError:
        message = text
    return int(code), str(message)


def scenario_guard(args) -> Result:
    """backend 방어선 (SPEC 6.2). AI가 잘못 제안해도 계약·구조 제약을 넘는 조치는 실행되지 않는다."""
    r = Result("backend 방어선 (계약·구조 제약)")
    incident = {t: sql(f"select max(id) from app.incidents where tenant_id = '{t}'") for t in ("tenant-a", "tenant-b")}
    if not all(incident.values()):
        r.check(False, "검사에 쓸 인시던트가 없다 (시나리오를 한 번 이상 돌린 뒤 실행한다)")
        return r
    cases = [
        ("guard-scale", "tenant-a", {"action": "scale_deployment", "service": "order-api", "replicas": 6},
         "2~4", "A사 확장 6 → 안전 상한 4로 거부"),
        ("guard-min", "tenant-a", {"action": "scale_deployment", "service": "order-api", "replicas": 1},
         "2~4", "A사 축소 1 → 계약 하한 2로 거부"),
        ("guard-runtime", "tenant-b", {"action": "restart_pods", "service": "settlement-batch"},
         "docker", "B사 Docker 서비스 재시작 → 지원하지 않는 실행 환경으로 거부"),
        ("guard-contract", "tenant-b", {"action": "rollback_deployment", "service": "reservation-api"},
         "허용되지 않는다", "B사 롤백 → 계약상 불가로 거부"),
    ]
    for _, tenant, body, expect, label in cases:
        code, message = internal_action(tenant, {**body, "incidentId": int(incident[tenant])})
        r.check(code == 422 and expect in message, f"{label} ({code}: {message[:70]})")
    replicas = kubectl("-n", "tenant-a", "get", "deploy", "order-api", "-o", "jsonpath={.spec.replicas}")
    r.check(replicas == "2", f"거부된 요청은 클러스터를 바꾸지 않았다 (A사 레플리카 {replicas})")
    return r


def scenario_question(args) -> Result:
    r = Result("문서 질문 (RAG)")
    started = time.time()
    engineer = Session("engineer")
    status, body = engineer.call("POST", "/api/ai/tasks",
                                 {"tenantId": "tenant-a", "question": "A사 SLA 계약서에서 P1 장애 응답 기한은 몇 분이야?"})
    if not r.check(status == 202, f"질문 접수 → {status}"):
        return r
    task = drive(body["taskId"], Expect(action_tool="-"), r, args.timeout, merge=False)
    summarize(task, r)
    answer = task.get("answer") or ""
    r.check("15" in answer, "답변에 계약 기한(15분)이 있다")
    r.check(any("sla" in (s.get("doc") or "") for s in task.get("sources") or []), "출처에 SLA 계약서가 있다")
    r.seconds = time.time() - started
    return r


def scenario_no_issue(args) -> Result:
    """열린 인시던트가 없는 고객사에 묻는 질문은 진단·조치 없이 이상 없음으로 끝난다 (기능별-변경사항 T7-5)."""
    r = Result("이상 없음 (열린 인시던트가 없는 질문)")
    started = time.time()
    tenant = sql("select id from app.tenants t where id in ('tenant-a','tenant-b') and not exists "
                 "(select 1 from app.incidents i where i.tenant_id = t.id and i.status = 'OPEN') order by id limit 1")
    if not r.check(bool(tenant), "열린 인시던트가 없는 고객사가 있다"):
        r.notes.append("A사·B사 모두 열린 인시던트가 있다: " + sql(
            "select string_agg('#'||id||' '||tenant_id, ', ') from app.incidents where status = 'OPEN'"))
        return r
    name = sql(f"select name from app.tenants where id = '{tenant}'")
    say(f"{name}({tenant})에 질문한다")
    # 문서 질문 단어(SLA, 계약, 런북 등)가 있으면 문서 검색으로 가므로 넣지 않는다
    engineer = Session("engineer")
    status, body = engineer.call("POST", "/api/ai/tasks", {"tenantId": tenant, "question": "지금 서비스 상태 괜찮아?"})
    if not r.check(status == 202, f"질문 접수 → {status}"):
        return r
    task = drive(body["taskId"], Expect(action_tool="-"), r, args.timeout, merge=False)
    summarize(task, r)
    answer = task.get("answer") or ""
    r.check(answer.startswith("## 이상 없음") and name in answer,
            f"답변이 '## 이상 없음'으로 시작하고 고객사 이름을 담는다 ({answer[:40]!r})")
    events = engineer.get(f"/api/ai/tasks/{task['id']}/events")
    approvals = sum(e["type"] == "APPROVAL_REQUIRED" for e in events)
    r.check(approvals == 0, f"승인 요청이 없다 ({approvals}건)")
    nodes = sorted({str((e.get("data") or {}).get("node")) for e in events if e["type"] == "TOOL_START"})
    r.check(nodes == ["load_context"], f"컨텍스트 수집 도구만 불렀다 (Node: {', '.join(nodes) or '없음'})")
    r.seconds = time.time() - started
    r.check(r.seconds <= 30, f"30초 안에 끝났다 ({r.seconds:.0f}초)")
    return r


def scenario_b(args) -> Result:
    r = Result("B. 파드 멈춤 → 재시작 (B사, DIRECT)")
    started, since = time.time(), datetime.now(timezone.utc)
    pods_before = kubectl("-n", "tenant-b", "get", "pods", "-l", "app=reservation-api", "-o", "name")
    inject("tenant-b", "WORKER_HANG")
    say("B사 reservation-api에 WORKER_HANG 주입")
    try:
        _, task = wait_for_incident_task(Session("admin"), "tenant-b", since, args.timeout)
        expect = Expect("business_execute_runbook_action", "restart_pods", "reservation-api")
        task = drive(task["id"], expect, r, args.timeout, merge=False)
        summary = summarize(task, r)
        check_proposals(task["id"], expect, r)
        action = summary.get("action") or {}
        r.check((action.get("result") or {}).get("status") == "EXECUTED", "재시작이 Kubernetes API로 실행됐다")
        r.check(summary.get("recovered") is True, "회복 확인: RECOVERED")
        pods_after = kubectl("-n", "tenant-b", "get", "pods", "-l", "app=reservation-api", "-o", "name")
        r.check(pods_after and pods_after != pods_before, "파드가 새로 만들어졌다")
    finally:
        reset_b()
    r.seconds = time.time() - started
    return r


def scenario_b2(args) -> Result:
    r = Result("B2. 외부 결제 API 지연 → 고객 공지 (B사)")
    started, since = time.time(), datetime.now(timezone.utc)
    inject("tenant-b", "PAYMENT_LATENCY")
    say("B사 reservation-api에 PAYMENT_LATENCY 주입")
    try:
        _, task = wait_for_incident_task(Session("admin"), "tenant-b", since, args.timeout)
        expect = Expect("business_send_customer_notice")
        task = drive(task["id"], expect, r, args.timeout, merge=False)
        summary = summarize(task, r)
        check_proposals(task["id"], expect, r)
        r.check(((summary.get("notice_result") or {}).get("status")) == "SENT", "고객 공지가 발송됐다")
        restarted = sql(f"select count(*) from app.audit_logs where tenant_id = 'tenant-b' "
                        f"and action = 'ACTION_EXECUTED' and created_at > '{since.isoformat()}'")
        r.check(restarted == "0", "외부 원인이라 재시작하지 않았다 (RB-B-01)")
    finally:
        reset_b()
    r.seconds = time.time() - started
    return r


def scenario_a(args) -> Result:
    r = Result("A. 잘못된 설정 배포 → 롤백 PR (A사, GITOPS)")
    started, since = time.time(), datetime.now(timezone.utc)
    say("A사 설정 저장소에 풀 크기 5 커밋을 푸시 (main에 기록이 남는다)")
    say(sh(str(ROOT / "scripts/scenario-a.sh")))
    try:
        _, task = wait_for_incident_task(Session("admin"), "tenant-a", since, args.timeout)
        expect = Expect("business_execute_runbook_action", "rollback_deployment", "order-api")
        task = drive(task["id"], expect, r, args.timeout, merge=not args.no_merge)
        summary = summarize(task, r)
        check_proposals(task["id"], expect, r)
        events = Session("engineer").get(f"/api/ai/tasks/{task['id']}/events")
        synced = any(e["type"] == "CHANGE_FINISHED" and (e.get("data") or {}).get("result") == "SYNCED" for e in events)
        r.check(synced, "PR 병합 후 ArgoCD 동기화 알림으로 재개됐다")
        r.check(summary.get("recovered") is True, "회복 확인: RECOVERED")
    finally:
        size = kubectl("-n", "tenant-a", "get", "deploy", "order-api", "-o",
                       "jsonpath={.spec.template.spec.containers[0].env[?(@.name==\"DEMO_POOL_SIZE\")].value}")
        r.check(size == "20", f"클러스터의 풀 크기가 20으로 돌아왔다 (현재 {size})")
        if size != "20":
            r.notes.append("A사 풀 크기가 원래대로가 아니다. gitops/tenants/tenant-a/order-api.yaml을 20으로 되돌리는 커밋이 필요하다")
        sh("git", "pull", "-q", "--ff-only", check=False)
    r.seconds = time.time() - started
    return r


def scenario_a_hang(args) -> Result:
    r = Result("A-hang. 파드 멈춤 → 순차 재시작 (A사, GITOPS)")
    started, since = time.time(), datetime.now(timezone.utc)
    pods_before = kubectl("-n", "tenant-a", "get", "pods", "-l", "app=order-api", "-o", "name")
    inject("tenant-a", "WORKER_HANG")
    say("A사 order-api에 WORKER_HANG 주입")
    try:
        _, task = wait_for_incident_task(Session("admin"), "tenant-a", since, args.timeout)
        expect = Expect("business_execute_runbook_action", "restart_pods", "order-api")
        task = drive(task["id"], expect, r, args.timeout, merge=False)
        summary = summarize(task, r)
        proposal = check_proposals(task["id"], expect, r) or {}
        impact = proposal.get("impact") or {}
        r.check(impact.get("restartMode") == "ROLLING", "승인 카드가 순차 재시작임을 알린다")
        r.check(impact.get("zeroDowntime") is True, "승인 카드가 전체 서비스 중단 없음을 알린다")
        r.check((impact.get("replicas") or 0) >= 2, "승인 카드에 현재 레플리카가 표시된다")
        action = summary.get("action") or {}
        action_result = action.get("result") or {}
        r.check(action_result.get("status") == "EXECUTED", "재시작이 Kubernetes API로 실행됐다")
        r.check(action_result.get("restartMode") == "ROLLING", "실제 조치도 파드를 하나씩 재시작했다")
        r.check(summary.get("recovered") is True, "회복 확인: RECOVERED")
        pods_after = kubectl("-n", "tenant-a", "get", "pods", "-l", "app=order-api", "-o", "name")
        r.check(pods_after and pods_after != pods_before, "파드가 새로 만들어졌다")
    finally:
        reset_a()
    r.seconds = time.time() - started
    return r


def reset_a() -> None:
    """A사 장애를 정리한다. 남은 장애가 다음 시나리오에 섞이지 않게 한다."""
    try:
        inject("tenant-a", None)
    except RuntimeError as e:
        say(f"⚠️  A사 장애 해제 실패: {e}")


def reset_b() -> None:
    """B사 장애를 정리한다. 남은 장애가 다음 시나리오에 섞이지 않게 한다."""
    try:
        inject("tenant-b", None)
    except RuntimeError as e:
        say(f"⚠️  B사 장애 해제 실패: {e}")


SCENARIOS = {
    "isolation": ("D. 고객사 격리 (권한·감사 로그)", scenario_isolation),
    "guard": ("backend 방어선 (안전 상한·실행 환경·계약)", scenario_guard),
    "question": ("문서 질문 → RAG 답변", scenario_question),
    "no-issue": ("열린 인시던트가 없는 질문 → 이상 없음", scenario_no_issue),
    "b": ("B. 파드 멈춤 → 재시작 (DIRECT)", scenario_b),
    "b2": ("B2. 외부 결제 지연 → 고객 공지", scenario_b2),
    "a": ("A. 잘못된 설정 배포 → 롤백 PR → 병합 → 동기화 (GITOPS)", scenario_a),
    "a-hang": ("A-hang. 파드 멈춤 → 순차 재시작 (GITOPS)", scenario_a_hang),
}


# ── 실행 ──────────────────────────────────────────────────────

def set_auto_approve(enabled: bool) -> bool | None:
    admin = Session("admin")
    status, body = admin.call("GET", "/api/settings")
    if status != 200:
        return None
    before = body.get("autoApprove")
    if before != enabled and body.get("autoApproveAllowed"):
        admin.call("PUT", "/api/settings/auto-approve", {"enabled": enabled})
    return before


def write_report(results: list[Result], started: datetime) -> Path:
    out = ROOT / ".local/e2e" / f"{started:%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# OpsLens E2E 결과 ({started:%Y-%m-%d %H:%M})", "", "| 시나리오 | 결과 | 시간 | 작업 |", "|---|---|---|---|"]
    for r in results:
        link = f"[{r.task_id[:8]}]({WEB}/tasks/{r.task_id})" if r.task_id else ""
        lines.append(f"| {r.name} | {'PASS' if r.passed else 'FAIL'} | {r.seconds:.0f}s | {link} |")
    for r in results:
        lines += ["", f"## {r.name}", ""] + [f"- {'✅' if ok else '❌'} {label}" for ok, label in r.checks]
        lines += [f"- 참고: {n}" for n in r.notes]
        if r.report:
            lines += ["", "<details><summary>보고서</summary>", "", r.report, "", "</details>"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help="실행할 시나리오 키 (생략하면 전체, --list로 확인)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-merge", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    if args.list:
        for key, (title, _) in SCENARIOS.items():
            print(f"  {key:10} {title}")
        return 0
    args.scenarios = args.scenarios or list(SCENARIOS)
    unknown = [s for s in args.scenarios if s not in SCENARIOS]
    if unknown:
        parser.error(f"알 수 없는 시나리오: {', '.join(unknown)} (--list로 확인)")

    lock = ROOT / ".local/e2e/.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        pid = lock.read_text().strip()
        if pid.isdigit() and subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0:
            print(f"다른 E2E 실행(PID {pid})이 진행 중이다. 끝난 뒤 다시 실행한다. 진행 상황: {WEB}")
            return 1
    lock.write_text(str(os.getpid()))
    try:
        return run_all(args)
    finally:
        lock.unlink(missing_ok=True)


def run_all(args) -> int:
    started = datetime.now()
    print(f"\n▶ 사전 점검")
    results = [preflight(args)]
    if not results[0].passed:
        print("\n사전 점검 실패. 위 항목을 고친 뒤 다시 실행한다 (클러스터: scripts/cluster-up.sh)")
        write_report(results, started)
        return 1

    previous = None if args.auto_approve else set_auto_approve(False)
    if previous:
        say("backend 자동 승인을 실행 동안 끈다 (끝나면 되돌린다)")
    try:
        for key in args.scenarios:
            title, run = SCENARIOS[key]
            print(f"\n▶ {title}")
            begin = time.time()
            try:
                result = run(args)
            except Exception as e:  # 한 시나리오가 실패해도 다음 시나리오는 돌린다
                result = Result(title)
                result.check(False, f"중단: {type(e).__name__}: {e}")
            result.seconds = result.seconds or time.time() - begin
            results.append(result)
    except KeyboardInterrupt:
        print("\n중단됨")
    finally:
        if previous:
            set_auto_approve(True)
            say("backend 자동 승인을 다시 켰다")

    print("\n━━ 결과 " + "━" * 50)
    for r in results:
        fails = [label for ok, label in r.checks if not ok]
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.name:52} {r.seconds:5.0f}s"
              + (f"\n        ↳ {fails[0]}" if fails else ""))
    report = write_report(results, started)
    print(f"\n  상세: {report.relative_to(ROOT)}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""OpsLens 전체 시나리오 E2E 테스트. 장애 주입부터 승인, 조치, 회복 확인, 보고서까지 실제로 돌리고 결과를 판정한다.

    python3 scripts/e2e.py                # 전체: 사전 점검 → 공통 묶음 → 서비스 묶음 5개 동시에 (장애 × 고객사 매트릭스)
    python3 scripts/e2e.py --lane B1      # 한 묶음만 (여러 번 줄 수 있다)
    python3 scripts/e2e.py b-hang a-dbslow  # 고른 시나리오만 (사전 점검은 항상). 예전 키 a·b·b2도 받는다
    python3 scripts/e2e.py --quick        # PR이 없는 빠른 확인: guard a-hang a-dbslow b-surge
    python3 scripts/e2e.py --list         # 시나리오 목록

옵션
    --no-merge       A사 PR(a-pool 롤백, a-surge 최대치)을 스크립트가 병합하지 않는다. 사람이 GitHub에서 병합할 때까지 기다린다
    --auto-approve   backend 자동 승인 설정을 그대로 쓴다. 기본은 실행 동안 끄고, 스크립트가 제안을 검사한 뒤 승인한다
    --timeout 초     시나리오 하나의 최대 시간 (기본 600)
    --no-branch      임시 브랜치를 쓰지 않는다 (A사 설정 저장소 기준이 main 그대로. a-pool·a-surge*는 실패한다)
    --cleanup        지난 실행이 남긴 임시 브랜치와 설정을 정리하고 끝낸다
    --serial         서비스 묶음을 동시에 돌리지 않고 하나씩 돌린다 (원인을 좁힐 때)
    --lane 묶음      그 묶음만 돈다 (공통, A1, A2, A3, B1, B2)

실행 순서: 공통 묶음(격리·문서 질문·이상 없음·근거 문서, 장애가 없을 때 돌린다) → 서비스 묶음을 동시에 (SPEC-e2e-speed 8.3).
묶음은 (고객사, 서비스)다: A1 order-api, A2 cart-api, A3 inventory-api, B1 reservation-api, B2 checkin-api.
한 서비스 안에서는 순서대로 돈다. 같은 서비스에 장애를 겹쳐 넣으면 증상이 섞이고, 같은 서비스의 알림은 인시던트 하나로
묶이기 때문이다. 서비스가 다르면 인시던트·관측·회복 확인·설정 이력이 모두 따로다. 시나리오가 끝나면 그 서비스의 장애를
모두 뺀다(부하 발생기 0, 풀 20, HPA 최대 3). HPA 축소는 평소 크기가 필요한 다음 시나리오만 기다린다 (NEEDS_NORMAL).

결과 파일 (.local/e2e/<시각>/, git에 올리지 않는다)
    summary.md       시나리오별 상태(대기·진행 중·통과·실패)와 시간. 실행 중에도 갱신된다
    <키>.log         그 시나리오의 진행 기록 전체
    <키>.md          판정 항목, 작업 링크, Agent 보고서

임시 브랜치 (SPEC-fault-based-actions 5.8): 실행 동안 A사 설정 저장소 기준을 e2e/<시각> 브랜치로 바꾼다
(backend 고객사 설정과 ArgoCD targetRevision). 장애 주입 커밋과 롤백 PR이 그 브랜치에서 일어나 main에 남지 않고,
실행 중 다른 사람이 main에 푸시해도 A사가 다시 동기화되지 않는다. 끝나면(실패·중단 포함) main으로 되돌리고 브랜치를 지운다.

승인 정책: 기대한 조치면 승인하고, 아니면 거절하고 실패로 기록한다. 잘못된 조치가 실행되지 않게 하기 위해서다.
비밀값은 출력하지 않는다(컨테이너 환경변수만 쓴다).
"""

import argparse
import contextlib
import contextvars
import threading
import base64
import os
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import inject as faults  # scripts/inject.py: 장애 주입·해제의 한 곳

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8080"
WEB = "http://localhost"
KCTX = "kind-opslens-tenants"
REPO = "bbbbb0413/ai-agent-capstone"
NODE_PORT = {"tenant-a": 30081, "tenant-b": 30082}
ADMIN_ONLY = {"business_send_customer_notice", "business_confirm_sla_credit"}
USERS = {"engineer": "engineer1234", "admin": "admin1234", "viewer-a": "viewer1234", "viewer-b": "viewer1234"}


# ── 출력 ──────────────────────────────────────────────────────

_print_lock = threading.Lock()
_log: contextvars.ContextVar["tuple[str, Path] | None"] = contextvars.ContextVar("e2e_log", default=None)
_scenario: contextvars.ContextVar[str | None] = contextvars.ContextVar("e2e_scenario", default=None)


def current_service() -> str | None:
    """지금 도는 시나리오의 서비스. 묶음(LANES)이 정한다."""
    return SERVICE_OF.get(_scenario.get() or "")


def say(msg: str) -> None:
    """진행 기록. 시나리오 안에서 부르면 그 시나리오의 로그 파일에도 쓰고, 화면에는 시나리오 키를 붙인다."""
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    current = _log.get()
    with _print_lock:
        if current:
            key, path = current
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            print(f"  [{key:12}] {line}", flush=True)
        else:
            print(f"  {line}", flush=True)


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
    """backend 컨테이너 안에서 셸을 실행한다. 내부 호출 토큰·GITHUB_TOKEN은 컨테이너 환경변수로만 쓴다."""
    return compose("exec", "-T", "backend", "sh", "-c", script)


def sql(query: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "opslens", "-tAc", query)


@contextlib.contextmanager
def probe_task(tenant: str):
    """Agent를 거치지 않는 내부 API 검사(방어선·격리)에 쓸 작업 토큰. (작업 ID, 토큰)

    backend는 실행 중인 작업에만 작업 토큰을 준다 (SPEC-limitations-fixes 3단계). 실행 중 상태의 검사용 작업을
    DB에 직접 만들고(전달 대기열에 넣지 않아 Agent는 실행하지 않는다) 토큰을 받은 뒤, 끝나면 지운다.
    """
    task_id = str(uuid.uuid4())
    sql(f"insert into app.ai_tasks (id, tenant_id, type, question, status) "
        f"values ('{task_id}', '{tenant}', 'QUESTION', 'E2E 내부 API 검사', 'RUNNING')")
    try:
        out = in_backend('curl -s -X POST -H "Authorization: Bearer $AGENT_BACKEND_TOKEN" '
                         f'http://localhost:8080/internal/tasks/{task_id}/token')
        yield task_id, json.loads(out)["token"]
    finally:
        sql(f"delete from app.ai_tasks where id = '{task_id}'")


def kubectl(*args: str, check: bool = True) -> str:
    return sh("kubectl", "--context", KCTX, *args, check=check)


class Session:
    """화면과 같은 공개 API를 같은 쿠키 방식으로 호출한다."""

    def __init__(self, user: str) -> None:
        self.user = user
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        status, _ = self.call("POST", "/api/auth/login", {"username": user, "password": USERS[user]})
        if status != 200:
            raise RuntimeError(f"{user} 로그인 실패 ({status})")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        # 화면과 같이 변경 요청에는 backend가 준 XSRF-TOKEN 쿠키 값을 헤더로 되돌려 보낸다 (CSRF)
        token = next((c.value for c in self.cookies if c.name == "XSRF-TOKEN"), None)
        if method not in ("GET", "HEAD") and token:
            headers["X-XSRF-TOKEN"] = token
        req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
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

    def upload(self, path: str, fields: dict[str, str], filename: str, content: bytes) -> tuple[int, object]:
        """화면의 파일 올리기와 같은 multipart 요청"""
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        token = next((c.value for c in self.cookies if c.name == "XSRF-TOKEN"), None)
        if token:
            headers["X-XSRF-TOKEN"] = token
        req = urllib.request.Request(API + path, data=b"".join(parts), method="POST", headers=headers)
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


def wait_for_closed_incidents(tenant: str, service: str, timeout: float = 420) -> None:
    """앞 장애의 인시던트가 닫힐 때까지 기다린다. 같은 서비스에 열린 인시던트가 있으면 새 알림이 거기에 묶여
    새 인시던트가 생기지 않는다 (SPEC-compound-faults 5장). 앞 장애의 로그는 Agent가 직전 인시던트가 닫힌 시각부터
    보므로 따로 쉬지 않는다 (SPEC-e2e-speed ②)."""
    deadline, said = time.time() + timeout, False
    while time.time() < deadline:
        open_ids = sql(f"select string_agg('#' || id, ', ') from app.incidents "
                       f"where tenant_id = '{tenant}' and service = '{service}' and status = 'OPEN'")
        if not open_ids:
            return
        if not said:
            say(f"앞 장애의 인시던트({open_ids})가 닫히기를 기다린다 (알림 해제 반영)")
            said = True
        time.sleep(3)
    say(f"⚠️  {timeout:.0f}초 안에 {tenant} {service}의 열린 인시던트가 닫히지 않았다. 새 장애가 그 인시던트로 합쳐질 수 있다")


def inject(tenant: str, kind: str | None, service: str, **options) -> None:
    """서비스에 장애를 주입한다 (kind는 scripts/inject.py의 종류). kind=None이면 그 서비스의 장애를 모두 해제한다."""
    if kind:
        wait_for_closed_incidents(tenant, service)
        say(faults.inject(tenant, kind, service=service, **options))
    else:
        say(faults.clear(tenant, service=service))   # 관리 API 장애, 부하 발생기, 풀 크기, HPA 최대치를 되돌린다


def github(method: str, path: str, body: dict | None = None) -> dict:
    return faults.github(method, path, body)


# ── 임시 브랜치 (SPEC 5.8) ─────────────────────────────────────


def gitops_state() -> tuple[str, str]:
    """(backend A사 기준 브랜치, ArgoCD tenant-a targetRevision)"""
    return (sql("select gitops_branch from app.tenants where id = 'tenant-a'"), faults.tracked_branch())


def leftover_branches() -> list[str]:
    refs = github("GET", "/git/matching-refs/heads/e2e/")
    return [r["ref"].removeprefix("refs/heads/") for r in refs] if isinstance(refs, list) else []


def switch_branch(branch: str) -> None:
    """A사 설정 저장소 기준을 branch로 바꾸고, ArgoCD가 그 브랜치 최신 커밋으로 동기화될 때까지 기다린다."""
    status, body = Session("admin").call("PUT", "/api/admin/tenants/tenant-a/gitops-branch", {"branch": branch})
    if status != 200:
        raise RuntimeError(f"backend 기준 브랜치 변경 실패 {status}: {str(body)[:200]}")
    kubectl("-n", "argocd", "patch", "application", "tenant-a", "--type", "merge",
            "-p", json.dumps({"spec": {"source": {"targetRevision": branch}}}))
    kubectl("-n", "argocd", "annotate", "application", "tenant-a", "argocd.argoproj.io/refresh=normal", "--overwrite")
    sha = github("GET", f"/git/ref/heads/{branch}")["object"]["sha"]
    deadline = time.time() + 180
    while time.time() < deadline:
        state = kubectl("-n", "argocd", "get", "application", "tenant-a", "-o",
                        "jsonpath={.status.sync.revision} {.status.sync.status} {.status.health.status}", check=False)
        if state == f"{sha} Synced Healthy":
            return
        time.sleep(5)
    raise TimeoutError(f"ArgoCD tenant-a가 {branch}({sha[:7]})로 동기화되지 않았다 (마지막 {state})")


def start_branch() -> str:
    branch = f"e2e/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    main_sha = github("GET", "/git/ref/heads/main")["object"]["sha"]
    out = github("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main_sha})
    if "ref" not in out:
        raise RuntimeError(f"임시 브랜치를 만들지 못했다: {str(out)[:200]}")
    switch_branch(branch)
    say(f"A사 설정 저장소 기준을 임시 브랜치 {branch}로 바꿨다 (main {main_sha[:7]}에서 시작)")
    return branch


def end_branch(branch: str | None) -> None:
    """main으로 되돌리고, 남은 임시 브랜치를 지운다. 실패해도 다음 단계는 계속한다 (사전 점검이 남은 것을 잡는다)."""
    try:
        if gitops_state() != ("main", "main"):
            switch_branch("main")
            say("A사 설정 저장소 기준을 main으로 되돌렸다 (ArgoCD 동기화 확인)")
    except Exception as e:
        say(f"⚠️  main으로 되돌리지 못했다: {e}. python3 scripts/e2e.py --cleanup")
    for name in [branch] if branch else leftover_branches():
        github("DELETE", f"/git/refs/heads/{name}")
        say(f"임시 브랜치 {name}을 지웠다")


# ── 작업 추적 ──────────────────────────────────────────────────

@dataclass
class Expect:
    """시나리오가 기대하는 Agent의 행동."""
    action_tool: str                       # 승인할 조치 도구
    action: str | None = None              # execute_runbook_action의 action
    service: str | None = None
    args: dict = field(default_factory=dict)   # 그 밖에 맞아야 하는 인자 (예: max_replicas)
    allow: set[str] = field(default_factory=lambda: {"business_escalate_incident"})


@dataclass
class Steps:
    """겹친 장애에서 기대하는 조치들 (SPEC-compound-faults 7.1). 승인은 이 중 하나면 하고, 순서는 끝난 뒤 판정한다."""
    steps: list[Expect]


def wait_for_incident_task(admin: Session, tenant: str, service: str, since: datetime,
                           timeout: float) -> tuple[dict, dict]:
    say(f"{tenant} {service} 알림 → 인시던트·작업 생성을 기다린다 (알림 규칙 for 15s + 평가 주기)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for inc in admin.get(f"/api/incidents?status=OPEN"):
            if (inc["tenantId"] == tenant and inc.get("service") == service
                    and datetime.fromisoformat(inc["startedAt"]) >= since):
                for task in admin.get("/api/ai/tasks"):
                    if task.get("incidentId") == inc["id"]:
                        say(f"인시던트 #{inc['id']} ({inc['severity']}) · 작업 {task['id'][:8]} · {WEB}/tasks/{task['id']}")
                        return inc, task
        time.sleep(3)
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


def expected(pending: dict, expect) -> bool:
    if isinstance(expect, Steps):   # 겹친 장애: 기대한 조치 중 하나면 승인한다. 순서는 끝난 뒤 check_steps가 본다
        return any(expected(pending, step) for step in expect.steps)
    tool, args = pending.get("tool"), pending.get("args") or {}
    if tool == "business_send_customer_notice" or not chosen(pending):   # 공지, 흐름이 낸 에스컬레이션(P1 등)
        return True
    if tool == expect.action_tool:
        wanted = {"action": expect.action, "service": expect.service, **expect.args}
        return all(args.get(k) == v for k, v in wanted.items() if v is not None)
    return tool in expect.allow


FLOW_CATEGORIES = {"P1", "NOT_RECOVERED", "CHANGE_TIMEOUT"}   # Agent 흐름이 정하는 에스컬레이션 (5.4.1)


def chosen(pending: dict) -> bool:
    """Agent가 조치로 고른 제안인가. 흐름이 정한 에스컬레이션(P1, 미회복, PR 기한 초과)이나 보고서 후 공지는 아니다."""
    if pending.get("tool") == "business_escalate_incident" and (pending.get("args") or {}).get("category") in FLOW_CATEGORIES:
        return False
    if pending.get("tool") == "business_send_customer_notice" and pending.get("reason") == "고객 공지 발송":
        return False
    return True


def describe(pending: dict) -> str:
    args = pending.get("args") or {}
    keys = ("action", "service", "category", "required_action", "blocked_by", "dependency", "max_replicas")
    return " ".join(str(x) for x in (pending.get("tool"), *(args.get(k) for k in keys)) if x)


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
    head = github("GET", f"/pulls/{number}").get("head", {}).get("ref")
    if out.get("merged") and head:   # 병합한 PR 브랜치가 원격에 쌓이지 않게 지운다 (PR 기록은 남는다)
        github("DELETE", f"/git/refs/heads/{head}")
        say(f"PR 브랜치 {head}를 지웠다")


def check_proposals(task_id: str, expect: Expect, result: Result) -> dict | None:
    """저장된 이벤트로 Agent가 낸 승인 요청을 검사한다. 자동 승인으로 진행돼도 같은 기준으로 판정한다."""
    events = Session("engineer").get(f"/api/ai/tasks/{task_id}/events")
    proposals = [e["data"] for e in events if e["type"] == "APPROVAL_REQUIRED" and e.get("data")]
    unique = list({str(p.get("tool")) + json.dumps(p.get("args"), sort_keys=True): p for p in proposals}.values())
    actions = [p for p in unique if p.get("tool") == expect.action_tool and chosen(p)]
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
            "alertmanager", "loki", "frontend", "seaweedfs", "document-indexer"}
    running = {line.split()[0] for line in services.splitlines() if line.endswith("running")}
    stack_ready = need <= running
    r.check(stack_ready, f"compose 서비스 실행 중 ({len(need & running)}/{len(need)})"
            + (f", 없음: {', '.join(sorted(need - running))}" if need - running else ""))
    ready = kubectl("get", "deploy", "-A", "-l", "app", "-o",
                    "jsonpath={range .items[*]}{.metadata.name}={.status.readyReplicas}/{.spec.replicas} {end}",
                    check=False)
    want = [svc for tenant, svc in LANE_TARGET.values()]
    r.check(all(f"{svc}=" in ready for svc in want), f"kind 고객사 서비스 ({ready.strip() or '응답 없음'})")
    # 새 클러스터에서는 HPA가 첫 CPU 지표를 받기 전까지 ArgoCD가 잠깐 Degraded로 본다. 바로 실패하지 않고 기다린다
    deadline = time.time() + 90
    while True:
        sync = kubectl("-n", "argocd", "get", "application", "tenant-a", "-o",
                       "jsonpath={.status.sync.status}/{.status.health.status}", check=False)
        if sync == "Synced/Healthy" or time.time() > deadline:
            break
        time.sleep(5)
    r.check(sync == "Synced/Healthy", f"ArgoCD tenant-a {sync or '없음'}")
    if "postgres" in running:
        # 공통 표준 런북은 근거 문서 저장소에 있다(SPEC-document-store). 검색 버전이 색인됐고 조각이 있어야 한다
        indexed = sql("select count(*) from app.documents d join app.document_versions v "
                      "on v.document_id = d.id and v.version = d.active_version "
                      "join docs.chunks c on c.tenant_id = d.tenant_id and c.doc_id = d.doc_id "
                      "and c.document_version = d.active_version "
                      "where d.tenant_id = '_common' and d.doc_id = 'runbook-standard' and d.deleted_at is null "
                      "and v.index_status = 'INDEXED'")
        r.check(indexed not in ("", "0"), "공통 표준 런북의 검색 버전이 색인됐다 (RAG)")
    else:
        r.check(False, "공통 표준 런북의 검색 버전이 색인됐다 (PostgreSQL 미실행)")
    if "postgres" in running:
        backend_branch, argocd_branch = gitops_state()
        left = leftover_branches() if "backend" in running else []
        ok = r.check((backend_branch, argocd_branch) == ("main", "main") and not left,
                     f"지난 실행의 임시 브랜치가 남지 않았다 (backend {backend_branch}, ArgoCD {argocd_branch}"
                     + (f", 남은 브랜치 {', '.join(left)}" if left else "") + ")")
        if not ok:
            r.notes.append("python3 scripts/e2e.py --cleanup 으로 정리한다")
    if GITHUB_SCENARIOS & set(args.scenarios):
        token = in_backend('[ -n "$GITHUB_TOKEN" ] && echo yes || echo no') if "backend" in running else "no"
        r.check(token == "yes", "backend에 GITHUB_TOKEN이 있다 (A사 PR)")
    loadgen = kubectl("-n", "traffic", "get", "deploy", "-o",
                      "jsonpath={range .items[*]}{.metadata.name}={.spec.replicas} {end}", check=False).split()
    r.check(loadgen and all(x.endswith("=0") for x in loadgen),
            f"부하 발생기가 꺼져 있다 ({' '.join(loadgen) or '없음. scripts/cluster-up.sh'})")
    for svc in sorted(faults.HPA_SERVICES):
        # 앞 실행 직후면 축소가 덜 끝났을 수 있다. 실패 대신 기다린다 (SPEC-e2e-speed ④)
        wait_for_a_normal(svc)
        hpa = kubectl("-n", "tenant-a", "get", "hpa", svc, "-o",
                      "jsonpath={.spec.minReplicas}~{.spec.maxReplicas} {.status.desiredReplicas}", check=False)
        r.check(hpa == "2~3 2", f"A사 {svc} HPA가 평소 상태다 (최소~최대 목표: {hpa or '없음'}, 기대 2~3 2)")
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


def rag_probe(task_id: str) -> dict:
    """B사 작업 토큰으로 rag-mcp를 직접 검색한다. 범위별 결과 문서 주소 {all: [...], common: [...]}"""
    # RAG: B사 작업 컨텍스트로 A사에만 있는 내용을 검색해도 A사 문서는 나오지 않고, 표준 런북은 나온다.
    # 표준 런북은 범위를 좁혀 따로 찾는다. 전체 범위 상위 10건은 쌓인 B사 회고(postmortem)가 채울 수 있다
    probe = compose("exec", "-T", "agent", "/app/.venv/bin/python", "-c", """
import asyncio, json
from agent.backend import backend
from agent.toolbox import load_tools, _as_text
TASK = "%s"
async def main():
    # Harness의 결과 길이 제한(4000자)을 거치지 않게 MCP 도구를 직접 부른다. 고객사는 작업 토큰으로만 정해진다
    token = await backend.task_token(TASK)
    tools = {t.name: t for t in await load_tools("tenant-b", TASK, "e2e", token)}
    query = "A사 DBA 연락, GitOps PR 병합, order-api"
    found = {}
    for scope in ("all", "common"):
        out = await tools["rag_search_documents"].ainvoke({"query": query, "top_k": 10, "scope": scope})
        found[scope] = [d["uri"] for d in json.loads(_as_text(out))]
    print(json.dumps(found))
asyncio.run(main())
""" % task_id)
    return json.loads(probe.splitlines()[-1]) if probe else {}


def rag_search(task_id: str, tenant: str, query: str, scope: str = "tenant") -> list[dict]:
    """그 고객사 작업 토큰으로 rag-mcp를 직접 검색한다. 결과 (uri, section_path, document_version, content 앞부분)"""
    out = compose("exec", "-T", "agent", "/app/.venv/bin/python", "-c", """
import asyncio, json, sys
from agent.backend import backend
from agent.toolbox import load_tools, _as_text
async def main(task, tenant, query, scope):
    tools = {t.name: t for t in await load_tools(tenant, task, "e2e", await backend.task_token(task))}
    out = await tools["rag_search_documents"].ainvoke({"query": query, "top_k": 5, "scope": scope})
    print(json.dumps([{k: d.get(k) for k in ("uri", "section_path", "document_version")} | {"content": d["content"][:400]}
                      for d in json.loads(_as_text(out))], ensure_ascii=False))
asyncio.run(main(*sys.argv[1:]))
""", task_id, tenant, query, scope)
    return json.loads(out.splitlines()[-1]) if out else []


def ascii_pdf(lines: list[str]) -> bytes:
    """글꼴을 넣지 않은 최소 PDF (ASCII 줄만). 공통 표준 런북 검사용 샘플이다"""
    ops = "BT\n" + "\n".join(f"/F1 11 Tf 1 0 0 1 50 {800 - 20 * i} Tm ({t}) Tj" for i, t in enumerate(lines)) + "\nET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(ops) + ops.encode() + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def contract_docx(marker: str) -> bytes:
    """B사 SLA 계약서 Word 샘플. python-docx가 있는 document-indexer 컨테이너에서 만든다"""
    out = compose("exec", "-T", "document-indexer", "/app/.venv/bin/python", "-c", """
import base64, io, sys, docx
d = docx.Document()
d.add_heading("B사 운영 대행 SLA 계약서 (E2E)", level=1)
d.add_paragraph("E2E 문서 반영 확인용 버전이다.")
d.add_heading("제1조 서비스 등급", level=2)
d.add_paragraph("등급: Silver")
d.add_heading("제9조 E2E 확인 조항", level=2)
d.add_paragraph("E2E 확인 문구 " + sys.argv[1] + " 는 이 버전에만 있다.")
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "항목", "값", "확인 문구", sys.argv[1]
buf = io.BytesIO(); d.save(buf); print(base64.b64encode(buf.getvalue()).decode())
""", marker)
    return base64.b64decode(out.splitlines()[-1])


def wait_for_index(who: Session, doc_id: str, version: int, timeout: float = 60) -> dict:
    """그 버전의 색인이 끝날 때까지(PENDING이 아닐 때까지) 기다린다. 버전 정보"""
    deadline = time.time() + timeout
    while True:
        detail = who.get(f"/api/documents/{doc_id}")
        found = next((v for v in detail["versions"] if v["version"] == version), {})
        if found.get("indexStatus") not in (None, "PENDING") or time.time() > deadline:
            return {**found, "activeVersion": detail["document"]["activeVersion"]}
        time.sleep(2)


def scenario_documents(args) -> Result:
    """근거 문서 저장소 (SPEC-document-store): 업로드 → 30초 안에 색인 → 검색 반영 → 되돌리기, 권한, 표준 런북 검사."""
    r = Result("근거 문서 업로드·색인·되돌리기")
    started = time.time()
    engineer, admin = Session("engineer"), Session("admin")
    docs = {d["document"]["docId"]: d for d in engineer.get("/api/documents?tenant=tenant-b")}
    if not r.check("sla-contract" in docs, "B사 SLA 계약서가 문서 저장소에 있다 (시드)"):
        return r
    sla = docs["sla-contract"]["document"]
    before = sla["activeVersion"]
    marker = f"OPSLENS-E2E-{uuid.uuid4().hex[:8].upper()}"

    # 1) 담당 엔지니어가 Word로 새 버전을 올리면 30초 안에 색인되고 그 고객사 작업의 검색에 나온다
    upload_at = time.time()
    status, body = engineer.upload(f"/api/documents/{sla['id']}/versions", {}, "b-sla-e2e.docx", contract_docx(marker))
    if not r.check(status == 201, f"엔지니어가 B사 SLA 계약서 새 버전(docx) 업로드 → {status}"):
        return r
    v = wait_for_index(engineer, sla["id"], body["version"], timeout=30)
    r.check(v.get("indexStatus") == "INDEXED",
            f"30초 안에 색인됐다 (v{body['version']} {v.get('indexStatus')}, {time.time() - upload_at:.0f}초"
            + (f", {v.get('indexError')}" if v.get("indexError") else "") + ")")
    with probe_task("tenant-b") as (probe_id, _):
        hits = rag_search(probe_id, "tenant-b", f"E2E 확인 문구 {marker}")
        top = hits[0] if hits else {}
        r.check(marker in top.get("content", "") and top.get("uri") == "docs://tenant-b/sla-contract",
                f"B사 작업의 문서 검색 1위가 새 조항이다 ({top.get('uri')}, {top.get('section_path')})")
        r.check("제9조 E2E 확인 조항" in (top.get("section_path") or ""), "검색 결과에 Word 제목 경로가 있다")

        # 2) 원래 버전으로 되돌리면 새 버전이 생기고 검색에서 새 조항이 빠진다
        status, restored = engineer.call("POST", f"/api/documents/{sla['id']}/versions/{before}/restore")
        r.check(status == 201, f"v{before}으로 되돌리기 → {status} (새 버전 v{(restored or {}).get('version')})")
        if status == 201:
            back = wait_for_index(engineer, sla["id"], restored["version"], timeout=30)
            r.check(back.get("indexStatus") == "INDEXED" and back.get("activeVersion") == restored["version"],
                    f"되돌린 버전이 검색 버전이다 (v{back.get('activeVersion')})")
            hits = rag_search(probe_id, "tenant-b", f"E2E 확인 문구 {marker}")
            r.check(not any(marker in h.get("content", "") for h in hits), "되돌린 뒤 검색에 새 조항이 없다")

    # 3) 권한: 엔지니어는 공통 문서를 바꾸지 못하고, 담당하지 않는 고객사도 마찬가지다(엔지니어는 A·B사 담당)
    common = {d["document"]["docId"]: d["document"] for d in admin.get("/api/documents?tenant=_common")}
    standard = common.get("runbook-standard")
    if not r.check(standard is not None, "공통 표준 런북이 문서 저장소에 있다 (시드)"):
        return r
    status, _ = engineer.upload(f"/api/documents/{standard['id']}/versions", {}, "runbook.md", b"# x\n\n## RB-STD-01\n")
    r.check(status == 403, f"엔지니어가 공통 표준 런북 업로드 → {status}")
    viewer = Session("viewer-b")
    status, _ = viewer.call("GET", "/api/documents?tenant=tenant-b")
    r.check(status == 403, f"고객사 담당자는 문서 관리 API를 쓰지 않는다 → {status}")

    # 4) 관리자가 조항 번호를 뺀 표준 런북 PDF를 올리면 색인이 거부되고 검색은 이전 버전으로 계속된다
    active = standard["activeVersion"]
    lines = ["MSP standard runbook (E2E, RB-STD-04 removed)"] + [
        line for i in range(1, 8) if i != 4 for line in (f"RB-STD-0{i} clause {i}", "Body text of the clause.")]
    status, body = admin.upload(f"/api/documents/{standard['id']}/versions", {}, "runbook-standard.pdf",
                                ascii_pdf(lines))
    if r.check(status == 201, f"관리자가 조항 번호를 뺀 표준 런북 PDF 업로드 → {status}"):
        bad = wait_for_index(admin, standard["id"], body["version"], timeout=30)
        r.check(bad.get("indexStatus") == "FAILED" and "RB-STD-04" in (bad.get("indexError") or ""),
                f"색인 거부: {bad.get('indexStatus')} [{bad.get('indexStage')}] {(bad.get('indexError') or '')[:60]}")
        r.check(bad.get("activeVersion") == active, f"검색은 이전 버전 v{active} 그대로다 (v{bad.get('activeVersion')})")
        # 화면에 실패한 최신 버전이 남지 않게 검색 버전을 다시 최신으로 올린다 (같은 내용이라 임베딩하지 않는다)
        status, again = admin.call("POST", f"/api/documents/{standard['id']}/versions/{active}/restore")
        if status == 201:
            wait_for_index(admin, standard["id"], again["version"], timeout=30)
    r.seconds = time.time() - started
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
    with probe_task("tenant-b") as (probe_id, b_token):
        code = in_backend('curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $MCP_BACKEND_TOKEN" '
                          f'-H "X-Task-Token: {b_token}" -H "X-Tenant-Id: tenant-a" '
                          'http://localhost:8080/internal/tenants/tenant-a/services')
        r.check(code == "403", f"B사 작업 토큰으로 A사 내부 API 호출 → {code} (헤더에 A사를 적어도)")
        code = in_backend('curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $MCP_BACKEND_TOKEN" '
                          '-H "X-Tenant-Id: tenant-a" http://localhost:8080/internal/tenants/tenant-a/services')
        r.check(code == "401", f"작업 토큰 없이 내부 API 호출 → {code}")
        rag = rag_probe(probe_id)
    # backend가 쓰는 B사 ServiceAccount 토큰 그대로. kubeconfig의 관리자 인증서가 섞이지 않게 빈 설정으로 호출한다
    server = kubectl("config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    can = subprocess.run(["kubectl", "--kubeconfig", "/dev/null", "--server", server,
                          "--certificate-authority", str(ROOT / ".local/tenants/ca.crt"),
                          "--token", (ROOT / ".local/tenants/tenant-b.token").read_text().strip(),
                          "auth", "can-i", "delete", "pods", "-n", "tenant-a"], capture_output=True, text=True)
    answer = can.stdout.strip() or can.stderr.strip()[:80]
    r.check(answer == "no", f"B사 ServiceAccount로 A사 파드 삭제 권한 → {answer}")
    uris = rag.get("all", [])
    r.check(uris and not any("tenant-a" in u for u in uris),
            f"B사 작업의 문서 검색에 A사 문서가 없다 ({len(uris)}건 중 A사 {sum('tenant-a' in u for u in uris)}건)")
    r.check(any("_common" in u for u in rag.get("common", [])), "B사 작업에서도 MSP 표준 런북은 검색된다")
    blocked = sql("select count(*) from app.audit_logs where action = 'TENANT_ISOLATION_BLOCKED' "
                  "and created_at > now() - interval '2 minutes'")
    r.check(blocked != "0", f"격리 차단이 감사 로그에 남았다 ({blocked}건)")
    return r


def internal_action(tenant: str, body: dict) -> tuple[int, str]:
    """business-mcp가 부르는 backend 내부 조치 API를 직접 부른다. Agent·승인을 거치지 않는 방어선 검사용."""
    payload = json.dumps(body).replace("'", "")
    with probe_task(tenant) as (_, token):
        out = in_backend(f'curl -s -w "\\n%{{http_code}}" -H "Authorization: Bearer $MCP_BACKEND_TOKEN" '
                         f'-H "X-Task-Token: {token}" -H "X-Tenant-Id: {tenant}" -H "Content-Type: application/json" '
                         f"-d '{payload}' http://localhost:8080/internal/tenants/{tenant}/actions")
    text, _, code = out.rpartition("\n")
    try:
        message = json.loads(text).get("message") or text
    except ValueError:
        message = text
    return int(code), str(message)


def scenario_guard(args) -> Result:
    prepare(SERVICE_OF["guard"])
    """backend 방어선 (SPEC 6.2). AI가 잘못 제안해도 계약·구조 제약을 넘는 조치는 실행되지 않는다."""
    r = Result("backend 방어선 (계약·구조 제약)")
    incident = {t: sql(f"select max(id) from app.incidents where tenant_id = '{t}'") for t in ("tenant-a", "tenant-b")}
    if not all(incident.values()):
        r.check(False, "검사에 쓸 인시던트가 없다 (시나리오를 한 번 이상 돌린 뒤 실행한다)")
        return r
    # A사 order-api는 HPA가 실제 레플리카를 관리한다. 직접 확장은 HPA와 GitOps가 덮어쓰므로
    # 거부해야 하며, 최대치 변경도 DB 연결 수로 계산한 안전 상한(4)을 넘을 수 없다.
    a_replicas_before = kubectl("-n", "tenant-a", "get", "deploy", "order-api", "-o", "jsonpath={.spec.replicas}")
    cases = [
        ("guard-hpa-scale", "tenant-a", {"action": "scale_deployment", "service": "order-api", "replicas": 4},
         "HPA", "A사 HPA 서비스 직접 확장 → HPA 충돌 방지를 위해 거부"),
        ("guard-hpa-max", "tenant-a", {"action": "adjust_autoscaler", "service": "order-api", "replicas": 5},
         "안전 상한 4", "A사 HPA 최대치 5 → 안전 상한 4로 거부"),
        ("guard-runtime", "tenant-b", {"action": "restart_pods", "service": "settlement-batch"},
         "docker", "B사 Docker 서비스 재시작 → 지원하지 않는 실행 환경으로 거부"),
        ("guard-contract", "tenant-b", {"action": "rollback_deployment", "service": "reservation-api"},
         "허용되지 않는다", "B사 롤백 → 계약상 불가로 거부"),
    ]
    for _, tenant, body, expect, label in cases:
        code, message = internal_action(tenant, {**body, "incidentId": int(incident[tenant])})
        r.check(code == 422 and expect in message, f"{label} ({code}: {message[:70]})")
    replicas = kubectl("-n", "tenant-a", "get", "deploy", "order-api", "-o", "jsonpath={.spec.replicas}")
    r.check(replicas == a_replicas_before,
            f"거부된 요청은 HPA가 관리하는 레플리카를 바꾸지 않았다 ({a_replicas_before} → {replicas})")
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


# 장애 × 고객사 매트릭스 (SPEC-fault-based-actions 6.1). 진행 순서는 같다: 장애 주입 → 알림 → 작업 → 제안 검사·승인 → 판정 → 정리

def fault_scenario(args, title: str, tenant: str, kind: str, expect: Expect, checks, merge: bool = False,
                   **inject_args) -> Result:
    """장애를 넣고 작업을 끝까지 진행한 뒤 checks(r, task, summary, proposal, incident, since)로 판정한다.
    서비스는 그 시나리오의 묶음이 정한다 (SERVICE_OF)."""
    r = Result(title)
    service = current_service()
    prepare(service)
    started, since = time.time(), datetime.now(timezone.utc)
    try:
        inject(tenant, kind, service, **inject_args)
        incident, task = wait_for_incident_task(Session("admin"), tenant, service, since, args.timeout)
        task = drive(task["id"], expect, r, args.timeout, merge=merge)
        summary = summarize(task, r)
        proposal = check_proposals(task["id"], expect, r) or {}
        checks(r, task, summary, proposal, incident, since)
    finally:
        reset(tenant, service)
    r.seconds = time.time() - started
    return r


def check_recovered(r: Result, summary: dict) -> None:
    r.check(summary.get("recovered") is True, "회복 확인: RECOVERED")


def check_notice(r: Result, summary: dict) -> None:
    r.check((summary.get("notice_result") or {}).get("status") == "SENT", "고객 공지가 발송됐다")


def check_no_app_action(r: Result, tenant: str, since: datetime, why: str) -> None:
    """서비스를 바꾸는 조치(재시작·확장·롤백·PR)가 실행되지 않았다. 같은 고객사의 다른 서비스 묶음 조치는 세지 않는다."""
    executed = sql(f"select count(*) from app.audit_logs where tenant_id = '{tenant}' "
                   f"and action in ('ACTION_EXECUTED', 'CHANGE_PR_CREATED') and created_at > '{since.isoformat()}' "
                   f"and (detail ->> 'incidentId')::bigint in (select id from app.incidents "
                   f"where tenant_id = '{tenant}' and service = '{current_service()}')")
    r.check(executed == "0", f"{why} 앱 조치를 실행하지 않았다 ({executed}건)")


def check_escalation(r: Result, incident: dict, category: str, recipient: str, **fields: str) -> None:
    """발송 대기열에 기대한 분류·받는 사람·인자로 들어갔다 (5.4.1)."""
    rows = json.loads(sql("select coalesce(json_agg(e), '[]') from (select category, blocked_by, required_action, "
                          f"dependency, recipients from app.escalations where incident_id = {incident['id']}) e") or "[]")
    found = next((e for e in rows if e["category"] == category
                  and all(e.get(k) == v for k, v in fields.items())), None)
    wanted = " ".join([category, *fields.values()])
    got = ", ".join(" ".join(str(v) for k, v in e.items() if v and k != "recipients") for e in rows) or "없음"
    r.check(bool(found), f"에스컬레이션 {wanted} 대기열 등록 (있음: {got})")
    names = [p.get("name") for p in (found or {}).get("recipients") or []]
    r.check(recipient in names, f"받는 사람에 {recipient}이 있다 ({', '.join(map(str, names)) or '없음'})")


def check_max_replicas(r: Result, task_id: str, limit: int) -> None:
    events = Session("engineer").get(f"/api/ai/tasks/{task_id}/events")
    asked = [((e.get("data") or {}).get("args") or {}).get("max_replicas") for e in events
             if e["type"] == "APPROVAL_REQUIRED"]
    over = [m for m in asked if isinstance(m, int) and m > limit]
    r.check(not over, f"안전 상한 {limit}을 넘는 최대치를 제안하지 않았다" + (f" (제안: {over})" if over else ""))


def scenario_a_pool(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        events = Session("engineer").get(f"/api/ai/tasks/{task['id']}/events")
        synced = any(e["type"] == "CHANGE_FINISHED" and (e.get("data") or {}).get("result") == "SYNCED" for e in events)
        r.check(synced, "PR 병합 후 ArgoCD 동기화 알림으로 재개됐다")
        check_recovered(r, summary)
        size = faults.pool_size("tenant-a", current_service())
        r.check(size == 20, f"클러스터의 풀 크기가 20으로 돌아왔다 (현재 {size})")

    # 풀 크기 5 커밋은 ArgoCD가 따라가는 임시 브랜치에 한다 (main에는 남지 않는다)
    return fault_scenario(args, "A-pool. 잘못된 설정 배포 → 롤백 PR (A사, GITOPS)", "tenant-a", "pool",
                          Expect("business_execute_runbook_action", "rollback_deployment", SERVICE_OF["a-pool"]),
                          checks, merge=not args.no_merge, size=5)


def scenario_a_hang(args) -> Result:
    service = SERVICE_OF["a-hang"]
    pods_before = kubectl("-n", "tenant-a", "get", "pods", "-l", f"app={service}", "-o", "name")

    def checks(r, task, summary, proposal, incident, since):
        impact = proposal.get("impact") or {}
        r.check(impact.get("restartMode") == "ROLLING", "승인 카드가 순차 재시작임을 알린다")
        r.check(impact.get("zeroDowntime") is True, "승인 카드가 전체 서비스 중단 없음을 알린다")
        r.check((impact.get("replicas") or 0) >= 2, "승인 카드에 현재 레플리카가 표시된다")
        action_result = (summary.get("action") or {}).get("result") or {}
        r.check(action_result.get("status") == "EXECUTED", "재시작이 Kubernetes API로 실행됐다")
        r.check(action_result.get("restartMode") == "ROLLING", "실제 조치도 파드를 하나씩 재시작했다")
        check_recovered(r, summary)
        pods_after = kubectl("-n", "tenant-a", "get", "pods", "-l", f"app={service}", "-o", "name")
        r.check(pods_after and pods_after != pods_before, "파드가 새로 만들어졌다")

    return fault_scenario(args, "A-hang. 파드 멈춤 → 순차 재시작 (A사, GITOPS)", "tenant-a", "hang",
                          Expect("business_execute_runbook_action", "restart_pods", service), checks)


def scenario_a_surge(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_max_replicas(r, task["id"], 4)
        hpa = faults.hpa_max(SERVICE_OF["a-surge"])
        r.check(hpa == 4, f"PR 병합·동기화 후 HPA 최대치가 4다 (현재 {hpa})")
        check_recovered(r, summary)

    # +250건/초: HPA 최대 3(약 210건/초)으로는 모자라고 4(약 280건/초)면 감당한다 (5.4.2)
    return fault_scenario(args, "A-surge. 트래픽 급증 → HPA 최대치 4 PR (A사, GITOPS)", "tenant-a", "surge",
                          Expect("business_execute_runbook_action", "adjust_autoscaler", SERVICE_OF["a-surge"],
                                 args={"max_replicas": 4}),
                          checks, merge=not args.no_merge)


def scenario_a_surge_max(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_max_replicas(r, task["id"], 4)
        r.check(summary.get("recovered") is False, "최대치 4로도 회복되지 않았다고 판정했다")
        check_escalation(r, incident, "ACTION_BLOCKED", "A사 운영 담당", blocked_by="STRUCTURE")
        check_notice(r, summary)

    # +400건/초: 안전 상한 4(약 280건/초)로도 모자란다. 더 올리면 DB 연결이 모자라 A사 판단이 필요하다
    return fault_scenario(args, "A-surge-max. 안전 상한으로도 부족 → 에스컬레이션 (A사)", "tenant-a", "surge",
                          Expect("business_execute_runbook_action", "adjust_autoscaler", SERVICE_OF["a-surge-max"],
                                 args={"max_replicas": 4}),
                          checks, merge=not args.no_merge, rate=400)


def scenario_a_latency(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_notice(r, summary)
        check_no_app_action(r, "tenant-a", since, "외부 원인이라")

    return fault_scenario(args, "A-latency. 외부 결제 API 지연 → 고객 공지 (A사)", "tenant-a", "latency",
                          Expect("business_send_customer_notice"), checks)


def scenario_a_dbslow(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_escalation(r, incident, "DEPENDENCY", "A사 DBA팀", dependency="postgres")
        check_notice(r, summary)
        check_no_app_action(r, "tenant-a", since, "DB 자체 지연이라")

    return fault_scenario(args, "A-dbslow. DB 자체 지연 → DBA 에스컬레이션 (A사)", "tenant-a", "dbslow",
                          Expect("business_escalate_incident", args={"category": "DEPENDENCY"}), checks)


def scenario_b_hang(args) -> Result:
    service = SERVICE_OF["b-hang"]
    pods_before = kubectl("-n", "tenant-b", "get", "pods", "-l", f"app={service}", "-o", "name")

    def checks(r, task, summary, proposal, incident, since):
        impact = proposal.get("impact") or {}
        r.check(impact.get("zeroDowntime") is False, "승인 카드가 재시작 중 전체 서비스 중단을 알린다 (레플리카 1)")
        action = summary.get("action") or {}
        r.check((action.get("result") or {}).get("status") == "EXECUTED", "재시작이 Kubernetes API로 실행됐다")
        check_recovered(r, summary)
        pods_after = kubectl("-n", "tenant-b", "get", "pods", "-l", f"app={service}", "-o", "name")
        r.check(pods_after and pods_after != pods_before, "파드가 새로 만들어졌다")

    return fault_scenario(args, "B-hang. 파드 멈춤 → 재시작 (B사, DIRECT)", "tenant-b", "hang",
                          Expect("business_execute_runbook_action", "restart_pods", service), checks)


def scenario_b_latency(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_notice(r, summary)
        check_no_app_action(r, "tenant-b", since, "외부 원인이라")

    return fault_scenario(args, "B-latency. 외부 결제 API 지연 → 고객 공지 (B사)", "tenant-b", "latency",
                          Expect("business_send_customer_notice"), checks)


def scenario_b_pool(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_escalation(r, incident, "ACTION_BLOCKED", "B사 운영 승인 담당",
                         required_action="rollback_deployment", blocked_by="CONTRACT")
        check_notice(r, summary)
        check_no_app_action(r, "tenant-b", since, "롤백이 계약상 불가라")

    # B사 운영자가 직접 잘못 배포한 상황 (kubectl set env). 롤백은 B사 승인이 필요하다
    return fault_scenario(args, "B-pool. 잘못된 배포 → 롤백 불가 에스컬레이션 (B사)", "tenant-b", "pool",
                          Expect("business_escalate_incident", args={"category": "ACTION_BLOCKED"}), checks, size=5)


def scenario_b_surge(args) -> Result:
    def checks(r, task, summary, proposal, incident, since):
        check_escalation(r, incident, "ACTION_BLOCKED", "B사 운영 승인 담당",
                         required_action="scale_deployment", blocked_by="CONTRACT")
        check_notice(r, summary)
        check_no_app_action(r, "tenant-b", since, "확장이 계약상 불가라")

    return fault_scenario(args, "B-surge. 트래픽 급증 → 확장 불가 에스컬레이션 (B사)", "tenant-b", "surge",
                          Expect("business_escalate_incident", args={"category": "ACTION_BLOCKED"}), checks)


# 겹친 장애 (SPEC-compound-faults 7장). 원인 후보와 순위는 Agent가 정한다. E2E가 그 판단이 기준대로였는지 본다

def cause_steps(task_id: str) -> list[dict]:
    """진단이 적은 원인 후보와 처리할 원인 (propose_action이 남긴 STEP). 진단마다 하나씩 있다."""
    events = Session("engineer").get(f"/api/ai/tasks/{task_id}/events")
    return [e["data"] for e in events if e["type"] == "STEP" and (e.get("data") or {}).get("causes") is not None]


def chosen_proposals(task_id: str) -> list[dict]:
    """Agent가 조치로 고른 승인 요청, 나온 순서대로 (같은 제안의 재요청은 한 번)."""
    events = Session("engineer").get(f"/api/ai/tasks/{task_id}/events")
    seen, out = set(), []
    for e in events:
        data = e.get("data") or {}
        key = str(data.get("tool")) + json.dumps(data.get("args"), sort_keys=True)
        if e["type"] == "APPROVAL_REQUIRED" and data and chosen(data) and key not in seen:
            seen.add(key)
            out.append(data)
    return out


def check_steps(r: Result, task_id: str, order: list[Expect], targets: list[str], causes: set[str]) -> None:
    """조치 순서, 진단마다의 처리할 원인, 원인 후보에 기대한 조항이 모두 있는지. 같은 원인에 앱 조치를 두 번 하지 않았는지."""
    steps = cause_steps(task_id)
    got = [s.get("target") for s in steps]
    r.check(got == targets, f"진단마다 처리할 원인이 기대와 같다 (기대 {' → '.join(targets)}, 실제 {' → '.join(map(str, got)) or '없음'})")
    named = {c for s in steps for c in s.get("causes") or []}
    r.check(causes <= named, f"원인 후보에 {', '.join(sorted(causes))}가 있다 (적힌 후보: {', '.join(sorted(named)) or '없음'})")
    proposals = chosen_proposals(task_id)
    ok = len(proposals) == len(order) and all(expected(p, e) for p, e in zip(proposals, order))
    r.check(ok, f"조치를 기대한 순서로 제안했다 ({' → '.join(describe(p) for p in proposals) or '제안 없음'})")
    actions = [(p.get("args") or {}).get("action") for p in proposals if p.get("tool") == "business_execute_runbook_action"]
    r.check(len(actions) == len(set(actions)), f"같은 앱 조치를 두 번 하지 않았다 ({', '.join(map(str, actions)) or '앱 조치 없음'})")


def check_one_incident(r: Result, tenant: str, since: datetime, incident: dict, task_id: str) -> None:
    """같은 서비스의 알림이 인시던트 하나로 묶였다 (5장). 덧붙은 알림은 작업 이벤트로 남는다."""
    opened = sql(f"select string_agg('#' || id, ', ') from app.incidents where tenant_id = '{tenant}' "
                 f"and service = '{incident.get('service')}' and started_at >= '{since.isoformat()}'")
    r.check(opened == f"#{incident['id']}", f"인시던트가 하나만 열렸다 ({opened or '없음'})")
    alerts = sql(f"select count(*) from app.incident_alerts where incident_id = {incident['id']}")
    added = sum(1 for e in Session("engineer").get(f"/api/ai/tasks/{task_id}/events") if e["type"] == "ALERT_ADDED")
    r.notes.append(f"인시던트 #{incident['id']}에 묶인 알림 {alerts}개, 작업에 남은 알림 추가 {added}건")


def reject_stray_tasks(tenant: str, service: str, since: datetime, task_id: str) -> None:
    """작업이 끝난 뒤 울린 알림은 새 인시던트·작업이 된다. 승인 대기로 남지 않게 거절해 끝낸다.
    같은 고객사의 다른 서비스 묶음 작업은 건드리지 않는다."""
    engineer = Session("engineer")
    mine = set(sql(f"select coalesce(string_agg(id::text, ','), '') from app.incidents "
                   f"where tenant_id = '{tenant}' and service = '{service}'").split(","))
    for task in engineer.get("/api/ai/tasks"):
        if task["id"] == task_id or task.get("tenantId") != tenant or task.get("status") != "WAITING_APPROVAL":
            continue
        if str(task.get("incidentId")) not in mine:
            continue
        if datetime.fromisoformat(task["createdAt"]) < since:
            continue
        pending = task.get("pendingAction") or engineer.get(f"/api/ai/tasks/{task['id']}").get("pendingAction") or {}
        who = Session("admin") if pending.get("tool") in ADMIN_ONLY else engineer
        who.call("POST", f"/api/ai/tasks/{task['id']}/approval",
                 {"decision": "REJECT", "comment": "e2e: 겹친 장애 시나리오가 끝난 뒤 생긴 작업", "approvalId": pending.get("approvalId")})
        say(f"작업이 끝난 뒤 생긴 작업 {task['id'][:8]}의 승인 요청을 거절했다")


def compound_scenario(args, title: str, tenant: str, kind: str, steps: Steps, checks, merge: bool = False,
                      **inject_args) -> Result:
    """겹친 장애를 넣고 작업을 끝까지 진행한 뒤 checks(r, task, summary, incident, since)로 판정한다."""
    r = Result(title)
    service = current_service()
    prepare(service)
    started, since = time.time(), datetime.now(timezone.utc)
    task_id = None
    try:
        inject(tenant, kind, service, **inject_args)
        incident, task = wait_for_incident_task(Session("admin"), tenant, service, since, args.timeout)
        task_id = task["id"]
        task = drive(task_id, steps, r, args.timeout, merge=merge)
        summary = summarize(task, r)
        checks(r, task, summary, incident, since)
        check_one_incident(r, tenant, since, incident, task_id)
    finally:
        if task_id:
            reject_stray_tasks(tenant, service, since, task_id)
        reset(tenant, service)
    r.seconds = time.time() - started
    return r


NOTICE = Expect("business_send_customer_notice")


def scenario_b_hang_latency(args) -> Result:
    service = SERVICE_OF["b-hang-latency"]
    restart = Expect("business_execute_runbook_action", "restart_pods", service)

    def checks(r, task, summary, incident, since):
        # 파드 멈춤이 결제 지연을 가린다. 재시작한 뒤 남은 결제 지연을 다시 진단해 공지한다 (외부 장애는 재시작해도 남는다).
        # B사는 결제 API가 의존 대상이라 로그 신호와 의존 대상 상태(DEGRADED)가 모두 근거다 (SPEC-e2e-speed ③)
        check_steps(r, task["id"], [restart, NOTICE], ["RB-STD-02", "RB-STD-04"], {"RB-STD-02", "RB-STD-04"})
        check_notice(r, summary)
        r.check(summary.get("recovered") is False, "재시작 뒤 결제 지연이 남아 회복되지 않았다고 판정했다")
        left = faults.dependency_faults("tenant-b", service)
        r.check("PAYMENT_LATENCY" in left, f"재시작한 새 파드도 결제 지연을 이어받았다 (ConfigMap {sorted(left)})")

    return compound_scenario(args, "B-hang+latency. 파드 멈춤 + 결제 지연 → 재시작 후 공지 (B사)", "tenant-b",
                             "hang+latency", Steps([restart, NOTICE]), checks)


def scenario_b_pool_dbslow(args) -> Result:
    blocked = Expect("business_escalate_incident",
                     args={"category": "ACTION_BLOCKED", "required_action": "rollback_deployment"})

    def checks(r, task, summary, incident, since):
        # 직전 변경이 있으니 풀 고갈은 DB 지연의 증상이 아니다. 앱 원인(롤백, 계약상 불가)을 먼저 처리한다
        check_steps(r, task["id"], [blocked], ["RB-STD-01"], {"RB-STD-01", "RB-STD-05"})
        check_escalation(r, incident, "ACTION_BLOCKED", "B사 운영 승인 담당",
                         required_action="rollback_deployment", blocked_by="CONTRACT")
        check_no_app_action(r, "tenant-b", since, "롤백이 계약상 불가라")

    # DB 지연을 먼저 넣는다. 잘못된 배포로 뜨는 새 파드가 ConfigMap에서 DB 지연을 이어받는다
    return compound_scenario(args, "B-pool+dbslow. 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (B사)", "tenant-b",
                             "dbslow+pool", Steps([blocked]), checks, size=5)


def scenario_a_dbslow_backlog(args) -> Result:
    raise_max = Expect("business_execute_runbook_action", "adjust_autoscaler", SERVICE_OF["a-dbslow-backlog"],
                       args={"max_replicas": 4})
    dba = Expect("business_escalate_incident", args={"category": "DEPENDENCY"})

    def checks(r, task, summary, incident, since):
        # 대기열 경고를 DB 지연의 증상으로 볼지는 DB p95가 요청 p95에서 차지하는 비율(0.5)로 정한다 (3.2).
        # 데모 앱은 의존 호출을 처리 슬롯 밖에서 해서 비율이 그때그때 다르다. 진단이 받은 사실로 기대값을 정한다
        steps = cause_steps(task["id"])
        facts = {f.get("clause"): f for f in (steps[0].get("facts") if steps else None) or []}
        share = (facts.get("RB-STD-05") or {}).get("dependency_share")
        absorbed = isinstance(share, (int, float)) and share >= 0.5
        r.notes.append(f"DB p95 / 요청 p95 = {share} → {'대기열은 DB 지연의 증상' if absorbed else '두 원인 모두 독립'}")
        if absorbed:
            check_steps(r, task["id"], [dba], ["RB-STD-05"], {"RB-STD-05"})
        else:
            check_steps(r, task["id"], [raise_max, dba], ["RB-STD-03", "RB-STD-05"], {"RB-STD-03", "RB-STD-05"})
            check_max_replicas(r, task["id"], 4)
        check_escalation(r, incident, "DEPENDENCY", "A사 DBA팀", dependency="postgres")

    return compound_scenario(args, "A-dbslow+surge. DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (A사)", "tenant-a",
                             "dbslow+surge", Steps([raise_max, dba]), checks, merge=not args.no_merge)


def hpa_normal(service: str) -> tuple[bool, str]:
    state = kubectl("-n", "tenant-a", "get", "hpa", service, "-o",
                    "jsonpath={.spec.maxReplicas} {.status.currentReplicas}", check=False)
    ready = kubectl("-n", "tenant-a", "get", "deploy", service, "-o", "jsonpath={.status.readyReplicas}", check=False)
    return state == f"{faults.HPA_MAX_NORMAL} 2" and ready == "2", f"최대 현재: {state}, 준비 {ready}"


def wait_for_a_normal(service: str, timeout: float = 300) -> bool:
    """A사 HPA 서비스가 평소 크기로 돌아오기를 기다린다: 최대치 복원 커밋이 동기화되고(HPA 최대 3), 부하가 빠져 HPA가
    스스로 레플리카 2로 줄인다 (축소 안정화 60초). 확장 원복 PR은 없다 (결정 8)."""
    deadline, said = time.time() + timeout, False
    while time.time() < deadline:
        ok, state = hpa_normal(service)
        if ok:
            return True
        if not said:
            say(f"{service}가 HPA 최대 3·레플리카 2로 돌아오기를 기다린다 ({state})")
            said = True
        time.sleep(5)
    say(f"⚠️  {timeout:.0f}초 안에 {service}가 HPA 최대 3·레플리카 2로 돌아오지 않았다 ({state})")
    return False


# 평소 크기에서 시작해야 하는 시나리오 (SPEC-e2e-speed ④). HPA가 최대에 닿아야 최대치 조정을 제안하고,
# guard는 거부 전후의 레플리카를 비교한다. 나머지는 앞 시나리오의 축소를 기다리지 않는다
NEEDS_NORMAL = {"a-surge", "a-surge-max", "a-dbslow-backlog", "guard"}


def prepare(service: str) -> None:
    if _scenario.get() in NEEDS_NORMAL and service in faults.HPA_SERVICES:
        wait_for_a_normal(service)


def reset(tenant: str, service: str) -> None:
    """서비스의 장애를 정리한다. 설정만 되돌리고, HPA 축소는 필요한 다음 시나리오가 기다린다 (prepare)."""
    try:
        inject(tenant, None, service)
    except RuntimeError as e:
        say(f"⚠️  {tenant} {service} 장애 해제 실패: {e}")


SCENARIOS = {
    "isolation": ("D. 고객사 격리 (권한·감사 로그)", scenario_isolation),
    "guard": ("backend 방어선 (안전 상한·실행 환경·계약)", scenario_guard),
    "question": ("문서 질문 → RAG 답변", scenario_question),
    "no-issue": ("열린 인시던트가 없는 질문 → 이상 없음", scenario_no_issue),
    "documents": ("근거 문서 업로드 → 색인 → 검색 반영 → 되돌리기, 권한, 표준 런북 검사", scenario_documents),
    "a-pool": ("A사 잘못된 설정 배포 → 롤백 PR → 병합 → 동기화 (GITOPS)", scenario_a_pool),
    "a-hang": ("A사 파드 멈춤 → 순차 재시작", scenario_a_hang),
    "a-surge": ("A사 트래픽 급증 → HPA 최대치 4 PR → 병합 → 확장", scenario_a_surge),
    "a-surge-max": ("A사 트래픽 급증(안전 상한 초과) → 최대치 4 후 미회복 → 에스컬레이션", scenario_a_surge_max),
    "a-latency": ("A사 외부 결제 지연 → 고객 공지", scenario_a_latency),
    "a-dbslow": ("A사 DB 자체 지연 → DBA 에스컬레이션", scenario_a_dbslow),
    "b-hang": ("B사 파드 멈춤 → 재시작 (전체 중단 영향 표시)", scenario_b_hang),
    "b-latency": ("B사 외부 결제 지연 → 고객 공지", scenario_b_latency),
    "b-pool": ("B사 잘못된 배포 → 롤백 계약상 불가 → 에스컬레이션", scenario_b_pool),
    "b-surge": ("B사 트래픽 급증 → 확장 계약상 불가 → 에스컬레이션", scenario_b_surge),
    "b-hang-latency": ("B사 파드 멈춤 + 결제 지연 → 재시작 후 고객 공지 (겹친 장애)", scenario_b_hang_latency),
    "a-dbslow-backlog": ("A사 DB 지연 + 트래픽 급증 → 판단 기준대로 처리 (겹친 장애)", scenario_a_dbslow_backlog),
    "b-pool-dbslow": ("B사 잘못된 배포 + DB 지연 → 롤백 불가 에스컬레이션 (겹친 장애)", scenario_b_pool_dbslow),
}
ALIASES = {"a": "a-pool", "b": "b-hang", "b2": "b-latency", "a-hang-latency": "b-hang-latency"}   # 예전 키
QUICK = ["guard", "a-hang", "a-dbslow", "b-surge"]              # PR이 없는 빠른 확인 (6.3)
GITHUB_SCENARIOS = {"a-pool", "a-surge", "a-surge-max", "a-dbslow-backlog"}   # PR을 만들고 병합한다


# 묶음은 (고객사, 서비스)다 (SPEC-e2e-speed 8.3). 같은 묶음은 순서대로, 묶음끼리는 동시에 돈다.
# 서비스가 다르면 인시던트·관측·회복 확인·설정 이력이 모두 따로라 같은 고객사 안에서도 동시에 돌 수 있다.
# HPA 최대치 PR이 필요한 시나리오는 HPA가 있는 order-api·cart-api에 나눈다. guard는 앞 시나리오가 만든 A사·B사
# 인시던트 번호로 요청하므로 A1 끝에 둔다 (빈 DB에서도 돌게)
LANES = {
    "공통": ["isolation", "question", "no-issue", "documents"],
    "A1": ["a-surge", "a-surge-max", "guard"],
    "A2": ["a-pool", "a-dbslow-backlog"],
    "A3": ["a-hang", "a-latency", "a-dbslow"],
    "B1": ["b-hang", "b-pool", "b-surge", "b-pool-dbslow"],
    "B2": ["b-latency", "b-hang-latency"],
}
LANE_TARGET = {"A1": ("tenant-a", "order-api"), "A2": ("tenant-a", "cart-api"), "A3": ("tenant-a", "inventory-api"),
               "B1": ("tenant-b", "reservation-api"), "B2": ("tenant-b", "checkin-api")}
LANE_OF = {key: lane for lane, keys in LANES.items() for key in keys}
SERVICE_OF = {key: LANE_TARGET[lane][1] for lane, keys in LANES.items() if lane in LANE_TARGET for key in keys}


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


class Run:
    """한 번의 실행 결과 폴더. 상태가 바뀔 때마다 summary.md를 다시 쓴다 (실행 중에도 열어 볼 수 있다)."""

    def __init__(self, started: datetime, keys: list[str]):
        self.started = started
        self.dir = ROOT / ".local/e2e" / f"{started:%Y%m%d-%H%M%S}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state: dict[str, dict] = {k: {"status": "대기", "result": None} for k in keys}
        self.extra: list[Result] = []   # 사전 점검, 임시 브랜치 준비 같은 시나리오 밖의 결과
        self.write()

    def start(self, key: str) -> None:
        with self.lock:
            self.state[key]["status"] = "진행 중"
            self.state[key]["since"] = datetime.now()
        self.write()

    def finish(self, key: str, result: Result) -> None:
        with self.lock:
            self.state[key].update(status="통과" if result.passed else "실패", result=result)
        self.write_detail(key, result)
        self.write()

    def results(self) -> list[Result]:
        return self.extra + [s["result"] for s in self.state.values() if s["result"]]

    def write_detail(self, key: str, r: Result) -> None:
        link = f"{WEB}/tasks/{r.task_id}" if r.task_id else "없음"
        lines = [f"# {r.name}", "", f"- 결과: **{'통과' if r.passed else '실패'}** ({r.seconds:.0f}초)",
                 f"- 작업: {link}", f"- 진행 기록: [{key}.log]({key}.log)", "", "## 판정", ""]
        lines += [f"- {'✅' if ok else '❌'} {label}" for ok, label in r.checks]
        if r.notes:
            lines += ["", "## 참고", ""] + [f"- {n}" for n in r.notes]
        if r.report:
            lines += ["", "## Agent 보고서", "", r.report]
        (self.dir / f"{key}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write(self) -> None:
        with self.lock:
            lines = [f"# OpsLens E2E ({self.started:%Y-%m-%d %H:%M})", "",
                     f"갱신: {datetime.now():%H:%M:%S}", "",
                     "| 묶음 | 시나리오 | 상태 | 시간 | 처음 실패한 항목 | 상세 |", "|---|---|---|---|---|---|"]
            for r in self.extra:
                fails = [label for ok, label in r.checks if not ok]
                lines.append(f"| - | {r.name} | {'통과' if r.passed else '실패'} | {r.seconds:.0f}초 | "
                             f"{(fails or [''])[0]} | |")
            for key, st in self.state.items():
                r = st["result"]
                if r:
                    fails = [label for ok, label in r.checks if not ok]
                    took, first = f"{r.seconds:.0f}초", (fails or [""])[0]
                elif st.get("since"):
                    took, first = f"{(datetime.now() - st['since']).seconds}초째", ""
                else:
                    took, first = "", ""
                detail = f"[{key}.md]({key}.md) · [log]({key}.log)" if r else (f"[log]({key}.log)" if st.get("since") else "")
                lines.append(f"| {LANE_OF.get(key, '-')} | {SCENARIOS[key][0]} | {st['status']} | {took} | "
                             f"{first.replace('|', '/')} | {detail} |")
            (self.dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_lane(run: Run, keys: list[str], args) -> None:
    """한 묶음의 시나리오를 순서대로 돌린다. 한 시나리오가 실패해도 다음 시나리오는 돌린다."""
    for key in keys:
        title, scenario = SCENARIOS[key]
        token = _log.set((key, run.dir / f"{key}.log"))
        scenario_token = _scenario.set(key)
        run.start(key)
        say(f"▶ {title}")
        begin = time.time()
        try:
            result = scenario(args)
        except Exception as e:  # noqa: BLE001 — 한 시나리오의 오류가 다른 시나리오를 멈추지 않게 한다
            result = Result(title)
            result.check(False, f"중단: {type(e).__name__}: {e}")
        result.seconds = result.seconds or time.time() - begin
        say(f"■ {'통과' if result.passed else '실패'} ({result.seconds:.0f}초)")
        run.finish(key, result)
        _scenario.reset(scenario_token)
        _log.reset(token)


def run_all(args) -> int:
    started = datetime.now()
    run = Run(started, args.scenarios)
    print(f"\n결과 폴더: {run.dir.relative_to(ROOT)}/summary.md (실행 중에도 갱신된다)")
    print("\n▶ 사전 점검")
    pre = preflight(args)
    run.extra.append(pre)
    run.write()
    if not pre.passed:
        print("\n사전 점검 실패. 위 항목을 고친 뒤 다시 실행한다 (클러스터: scripts/cluster-up.sh)")
        return 1

    previous = None if args.auto_approve else set_auto_approve(False)
    if previous:
        say("backend 자동 승인을 실행 동안 끈다 (끝나면 되돌린다)")
    branch = None
    try:
        if not args.no_branch:
            branch = start_branch()
        lanes = {name: [k for k in keys if k in args.scenarios] for name, keys in LANES.items()}
        common = lanes.pop("공통")
        parallel = [keys for keys in lanes.values() if keys]
        run_lane(run, common, args)          # 장애가 없을 때 돌려야 하는 시나리오
        if args.serial:
            for lane in parallel:
                run_lane(run, lane, args)
        else:
            threads = [threading.Thread(target=run_lane, args=(run, lane, args), daemon=True) for lane in parallel]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):   # join(timeout)이라 Ctrl-C로 멈출 수 있다
                for t in threads:
                    t.join(timeout=1)
    except KeyboardInterrupt:
        print("\n중단됨")
    except Exception as e:  # noqa: BLE001 — 임시 브랜치 준비 실패 등. 아래 finally에서 되돌린다
        failed = Result("임시 브랜치 준비")
        failed.check(False, f"중단: {type(e).__name__}: {e}")
        run.extra.append(failed)
    finally:
        if not args.no_branch:
            end_branch(branch)
        if previous:
            set_auto_approve(True)
            say("backend 자동 승인을 다시 켰다")
        run.write()

    results = run.results()
    print("\n━━ 결과 " + "━" * 50)
    for r in results:
        fails = [label for ok, label in r.checks if not ok]
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.name:52} {r.seconds:5.0f}s"
              + (f"\n        ↳ {fails[0]}" if fails else ""))
    print(f"\n  상세: {(run.dir / 'summary.md').relative_to(ROOT)}")
    return 0 if results and all(r.passed for r in results) and len(results) == len(args.scenarios) + len(run.extra) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help="실행할 시나리오 키 (생략하면 전체, --list로 확인)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-merge", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--no-branch", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--serial", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--lane", action="append", choices=list(LANES),
                        help="그 묶음만 돈다. 여러 번 줄 수 있다 (SPEC-e2e-speed ⑥)")
    args = parser.parse_args()
    if args.list:
        for key, (title, _) in SCENARIOS.items():
            alias = next((a for a, k in ALIASES.items() if k == key), "")
            lane = LANE_OF[key]
            target = " ".join(LANE_TARGET.get(lane, ("", "")))
            print(f"  {key:16} {lane:4} {target:26} {title}" + (f" (예전 키 {alias})" if alias else ""))
        return 0
    if args.cleanup:
        end_branch(None)
        print(f"정리 완료: backend·ArgoCD {gitops_state()}, 남은 임시 브랜치 {leftover_branches() or '없음'}")
        return 0
    args.scenarios = [ALIASES.get(s, s) for s in args.scenarios] or (QUICK if args.quick else list(SCENARIOS))
    if args.lane:
        args.scenarios = [k for k in args.scenarios if LANE_OF[k] in args.lane]
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


if __name__ == "__main__":
    sys.exit(main())

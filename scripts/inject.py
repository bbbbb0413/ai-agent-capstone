#!/usr/bin/env python3
"""고객사 서비스에 장애를 넣고 뺀다 (SPEC-fault-based-actions 5.7). scripts/inject.sh가 이 파일을 부른다.

  scripts/inject.sh <tenant-a|tenant-b> <pool|hang|latency|dbslow|surge|clear> [--size N] [--allow-main]

  pool     커넥션 풀 크기를 줄인 잘못된 배포. A사(GITOPS)는 ArgoCD가 따라가는 브랜치에 커밋, B사(DIRECT)는 직접 배포
  hang     작업 스레드 고갈 (데모 앱 관리 API WORKER_HANG)
  latency  외부 결제 API 지연 (PAYMENT_LATENCY)
  dbslow   DB 자체 지연 (DB_SLOW)
  surge    트래픽 급증 (traffic 네임스페이스의 부하 발생기 레플리카 1)
  clear    위를 모두 원래대로 (관리 API 장애 해제, 부하 발생기 0, 풀 크기 20)

A사 pool 커밋은 ArgoCD Application tenant-a가 따라가는 브랜치에 한다. 그 브랜치가 main이면 --allow-main 없이는 거부한다.
E2E는 실행 동안 임시 브랜치(e2e/<시각>)를 쓰므로 main에 기록이 남지 않는다 (5.8, 결정 4).
비밀 값(SERVICE_TOKEN, GITHUB_TOKEN)은 backend 컨테이너 환경변수로만 쓰고 이 스크립트는 읽지 않는다.
"""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KCTX = "kind-opslens-tenants"
REPO = "bbbbb0413/ai-agent-capstone"
NODE_PORT = {"tenant-a": 30081, "tenant-b": 30082}
SERVICE = {"tenant-a": "order-api", "tenant-b": "reservation-api"}
POOL_FILE = "gitops/tenants/tenant-a/order-api.yaml"
POOL_NORMAL = 20
FAULTS = {"hang": "WORKER_HANG", "latency": "PAYMENT_LATENCY", "dbslow": "DB_SLOW"}
KINDS = ["pool", *FAULTS, "surge", "clear"]


def run(*cmd: str, input: str | None = None, check: bool = True) -> str:
    out = subprocess.run(cmd, input=input, capture_output=True, text=True, cwd=ROOT)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} … 실패: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout.strip()


def kubectl(*args: str, check: bool = True) -> str:
    return run("kubectl", "--context", KCTX, *args, check=check)


def in_backend(script: str, input: str | None = None) -> str:
    """backend 컨테이너 안에서 실행한다. 고객사 NodePort와 GitHub 토큰은 거기서만 닿는다."""
    return run("docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "exec", "-T", "backend",
               "sh", "-c", script, input=input)


def github(method: str, path: str, body: dict | None = None) -> dict | list:
    """GitHub REST API. 본문은 stdin으로 넘겨 셸 따옴표 문제를 피한다."""
    data = "-d @-" if body is not None else ""
    out = in_backend(f'curl -s -X {method} -H "Authorization: Bearer $GITHUB_TOKEN" '
                     f'-H "Accept: application/vnd.github+json" {data} https://api.github.com/repos/{REPO}{path}',
                     input=json.dumps(body) if body is not None else None)
    return json.loads(out or "{}")


# ── 데모 앱 관리 API ──────────────────────────────────────────


def set_fault(tenant: str, fault: str | None) -> None:
    url = f"http://opslens-tenants-control-plane:{NODE_PORT[tenant]}/admin/faults"
    method = f"-X POST -H 'Content-Type: application/json' -d '{{\"fault\":\"{fault}\"}}'" if fault else "-X DELETE"
    in_backend(f'curl -sf {method} -H "Authorization: Bearer $SERVICE_TOKEN" {url} > /dev/null')


def set_surge(tenant: str, on: bool) -> None:
    kubectl("-n", "traffic", "scale", f"deploy/loadgen-{tenant}", f"--replicas={1 if on else 0}")


# ── 커넥션 풀 (잘못된 배포) ─────────────────────────────────────


def tracked_branch() -> str:
    """ArgoCD Application tenant-a가 따라가는 브랜치."""
    return kubectl("-n", "argocd", "get", "application", "tenant-a", "-o", "jsonpath={.spec.source.targetRevision}")


def pool_size(tenant: str) -> int | None:
    value = kubectl("-n", tenant, "get", "deploy", SERVICE[tenant], "-o",
                    'jsonpath={.spec.template.spec.containers[0].env[?(@.name=="DEMO_POOL_SIZE")].value}', check=False)
    return int(value) if value.isdigit() else None


def set_pool(tenant: str, size: int, allow_main: bool = False) -> str:
    if tenant == "tenant-b":
        # B사 운영자가 직접 잘못 배포한 상황. 새 ReplicaSet이 생겨 배포 이력(롤백 대상)이 된다
        kubectl("-n", "tenant-b", "set", "env", f"deploy/{SERVICE[tenant]}", f"DEMO_POOL_SIZE={size}")
        return f"B사 reservation-api에 DEMO_POOL_SIZE={size} 배포"
    branch = tracked_branch()
    if branch == "main" and not allow_main:
        raise SystemExit("ArgoCD tenant-a가 main을 따라간다. main에 장애 커밋을 남기려면 --allow-main을 준다 "
                         "(E2E는 임시 브랜치를 쓴다)")
    current = github("GET", f"/contents/{POOL_FILE}?ref={branch}")
    text = base64.b64decode(current["content"]).decode()
    updated = re.sub(r'(name: DEMO_POOL_SIZE\s*\n\s*value: ")\d+(")', rf"\g<1>{size}\g<2>", text, count=1)
    if updated == text:
        return f"{branch}의 풀 크기가 이미 {size}다"
    commit = github("PUT", f"/contents/{POOL_FILE}", {
        "message": f"Tune order-api DB connection pool to {size}", "branch": branch, "sha": current["sha"],
        "content": base64.b64encode(updated.encode()).decode()})
    sha = (commit.get("commit") or {}).get("sha", "")
    if not sha:
        raise RuntimeError(f"커밋 실패: {str(commit)[:200]}")
    return f"{branch}에 풀 크기 {size} 커밋 {sha[:7]} (ArgoCD가 30초 안에 반영한다)"


# ── 진입점 ────────────────────────────────────────────────────


def inject(tenant: str, kind: str, size: int = 5, allow_main: bool = False) -> str:
    if kind == "pool":
        return set_pool(tenant, size, allow_main)
    if kind in FAULTS:
        set_fault(tenant, FAULTS[kind])
        return f"{tenant} {SERVICE[tenant]}에 {FAULTS[kind]} 주입"
    if kind == "surge":
        set_surge(tenant, True)
        return f"{tenant} 부하 발생기 켬"
    return clear(tenant, allow_main)


def clear(tenant: str, allow_main: bool = False) -> str:
    """주입한 장애를 모두 뺀다. 풀 크기는 클러스터 값이 20이 아닐 때만 되돌린다."""
    notes = []
    set_fault(tenant, None)
    set_surge(tenant, False)
    if pool_size(tenant) not in (None, POOL_NORMAL):
        try:
            notes.append(set_pool(tenant, POOL_NORMAL, allow_main))
        except SystemExit as e:
            notes.append(f"풀 크기 복원 안 함: {e}")
    return f"{tenant} 장애 해제" + (f" ({'; '.join(notes)})" if notes else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tenant", choices=list(SERVICE))
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--size", type=int, default=5, help="pool의 풀 크기 (기본 5, 정상은 20)")
    parser.add_argument("--allow-main", action="store_true", help="A사 pool 커밋을 main에 남기는 것을 허용한다")
    args = parser.parse_args()
    print(inject(args.tenant, args.kind, args.size, args.allow_main))
    return 0


if __name__ == "__main__":
    sys.exit(main())

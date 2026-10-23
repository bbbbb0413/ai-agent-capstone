#!/usr/bin/env python3
"""고객사 서비스에 장애를 넣고 뺀다 (SPEC-fault-based-actions 5.7). scripts/inject.sh가 이 파일을 부른다.

  scripts/inject.sh <tenant-a|tenant-b> <pool|hang|latency|dbslow|surge|clear> [--size N] [--rate N] [--allow-main]
  scripts/inject.sh tenant-a hang+latency      # 여러 장애를 겹쳐 넣는다 (SPEC-compound-faults 6.1)

  pool     커넥션 풀 크기를 줄인 잘못된 배포. A사(GITOPS)는 ArgoCD가 따라가는 브랜치에 커밋, B사(DIRECT)는 직접 배포
  hang     작업 스레드 고갈 (데모 앱 관리 API WORKER_HANG)
  latency  외부 결제 API 지연 (PAYMENT_LATENCY)
  dbslow   DB 자체 지연 (DB_SLOW)
  surge    트래픽 급증 (traffic 네임스페이스의 부하 발생기 레플리카 1). --rate로 초당 건수를 바꾼다
  clear    위를 모두 원래대로 (관리 API 장애 해제, 의존 장애 ConfigMap 비움, 부하 발생기 0·기본 건수, 풀 크기 20, A사 HPA 최대 3)

결제 지연·DB 지연은 앱 밖의 장애라 파드를 재시작해도 남아야 한다. 떠 있는 파드에는 관리 API로 바로 넣고, 새 파드가 시작할 때
읽도록 고객사 네임스페이스의 ConfigMap dependency-faults에도 적는다 (6.2). 파드 멈춤·풀 고갈은 재시작하면 풀린다.

A사 pool 커밋과 HPA 최대치 복원 커밋은 ArgoCD Application tenant-a가 따라가는 브랜치에 한다. 그 브랜치가 main이면 --allow-main 없이는 거부한다.
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
SERVICE = {"tenant-a": "order-api", "tenant-b": "reservation-api"}
POOL_FILE = "gitops/tenants/tenant-a/order-api.yaml"
POOL_NORMAL = 20
HPA_FILE = "gitops/tenants/tenant-a/hpa.yaml"
HPA_MAX_NORMAL = 3   # A사가 평소 쓰는 최대치. E2E가 adjust_autoscaler PR을 병합하면 올라가므로 clear가 되돌린다
LOADGEN_RATE = {"tenant-a": 250, "tenant-b": 120}   # gitops/cluster/traffic.yaml의 기본값
FAULTS = {"hang": "WORKER_HANG", "latency": "PAYMENT_LATENCY", "dbslow": "DB_SLOW"}
DEPENDENCY_FAULTS = {"PAYMENT_LATENCY", "DB_SLOW"}   # 데모 앱 FaultState.DEPENDENCY_FAULTS와 같다
DEPENDENCY_CONFIGMAP = "dependency-faults"
KINDS = ["pool", *FAULTS, "surge", "clear"]


def run(*cmd: str, input: str | None = None, check: bool = True) -> str:
    out = subprocess.run(cmd, input=input, capture_output=True, text=True, cwd=ROOT)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} … 실패: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout.strip()


def kubectl(*args: str, check: bool = True, input: str | None = None) -> str:
    return run("kubectl", "--context", KCTX, *args, input=input, check=check)


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
    """모든 파드에 장애를 더하거나 모두 뺀다. 장애 상태는 파드마다 따로 있어서, Service(NodePort)로 한 번 부르면
    파드 하나에만 닿는다. 그러면 A사(파드 2개)에서 해제가 다른 파드로 가 장애가 남는다 (a-latency 뒤 a-dbslow 실패).
    파드 안에서 자기 관리 API를 부른다. 토큰은 파드 환경변수로만 쓰고 이 스크립트는 읽지 않는다.
    더하기라 이미 있는 장애(예: 잘못된 배포의 풀 고갈)는 그대로 남는다."""
    method, path = ("-X POST", f"/admin/faults/{fault}") if fault else ("-X DELETE", "/admin/faults")
    pods = kubectl("-n", tenant, "get", "pods", "-l", f"app={SERVICE[tenant]}", "--field-selector=status.phase=Running",
                   "-o", "jsonpath={.items[*].metadata.name}").split()
    if not pods:
        raise RuntimeError(f"{tenant} {SERVICE[tenant]} 파드가 없다")
    for pod in pods:
        kubectl("-n", tenant, "exec", pod, "--", "sh", "-c",
                f'curl -sf {method} -H "Authorization: Bearer $SERVICE_TOKEN" http://localhost:8080{path} > /dev/null')


def dependency_faults(tenant: str) -> set[str]:
    value = kubectl("-n", tenant, "get", "configmap", DEPENDENCY_CONFIGMAP, "-o",
                    "jsonpath={.data.dependency-faults}", check=False)
    return {f for f in value.replace(",", " ").split() if f in DEPENDENCY_FAULTS}


def set_dependency_faults(tenant: str, faults: set[str]) -> None:
    """새 파드가 시작할 때 읽는 의존 대상 장애. 설정 저장소가 아니라 클러스터에 직접 둔다(ArgoCD가 관리하지 않는다)."""
    manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": DEPENDENCY_CONFIGMAP},
                "data": {"dependency-faults": ",".join(sorted(faults))}}
    kubectl("-n", tenant, "apply", "-f", "-", input=json.dumps(manifest))


def set_surge(tenant: str, on: bool, rate: int | None = None) -> None:
    """부하 발생기를 켜고 끈다. rate를 주면 초당 건수를 바꾸고, 응답 대기 상한도 건수에 맞춰 늘린다
    (상한이 그대로면 대기열이 길어져도 처리량이 상한/지연에 묶여 과부하가 덜 보인다). 끌 때는 기본값으로 되돌린다."""
    deploy = f"deploy/loadgen-{tenant}"
    if on and rate:
        kubectl("-n", "traffic", "set", "env", deploy, f"LOADGEN_RATE={rate}", f"LOADGEN_MAX_IN_FLIGHT={rate * 2}")
    kubectl("-n", "traffic", "scale", deploy, f"--replicas={1 if on else 0}")
    if not on:
        kubectl("-n", "traffic", "set", "env", deploy, f"LOADGEN_RATE={LOADGEN_RATE[tenant]}", "LOADGEN_MAX_IN_FLIGHT-")


# ── A사 설정 저장소 커밋 ───────────────────────────────────────


def tracked_branch() -> str:
    """ArgoCD Application tenant-a가 따라가는 브랜치."""
    return kubectl("-n", "argocd", "get", "application", "tenant-a", "-o", "jsonpath={.spec.source.targetRevision}")


def commit_tracked(path: str, pattern: str, value: int, message: str, allow_main: bool) -> str:
    """ArgoCD tenant-a가 따라가는 브랜치의 path에서 pattern(앞·뒤 두 그룹 사이의 숫자)을 value로 바꿔 커밋한다."""
    branch = tracked_branch()
    if branch == "main" and not allow_main:
        raise SystemExit("ArgoCD tenant-a가 main을 따라간다. main에 장애 커밋을 남기려면 --allow-main을 준다 "
                         "(E2E는 임시 브랜치를 쓴다)")
    current = github("GET", f"/contents/{path}?ref={branch}")
    text = base64.b64decode(current["content"]).decode()
    updated = re.sub(pattern, rf"\g<1>{value}\g<2>", text, count=1)
    if updated == text:
        return f"{branch}의 {path.rsplit('/', 1)[-1]} 값이 이미 {value}다"
    commit = github("PUT", f"/contents/{path}", {
        "message": message, "branch": branch, "sha": current["sha"],
        "content": base64.b64encode(updated.encode()).decode()})
    sha = (commit.get("commit") or {}).get("sha", "")
    if not sha:
        raise RuntimeError(f"커밋 실패: {str(commit)[:200]}")
    return f"{branch}에 커밋 {sha[:7]} ({message}. ArgoCD가 30초 안에 반영한다)"


# ── 커넥션 풀 (잘못된 배포) ─────────────────────────────────────


def pool_size(tenant: str) -> int | None:
    value = kubectl("-n", tenant, "get", "deploy", SERVICE[tenant], "-o",
                    'jsonpath={.spec.template.spec.containers[0].env[?(@.name=="DEMO_POOL_SIZE")].value}', check=False)
    return int(value) if value.isdigit() else None


def set_pool(tenant: str, size: int, allow_main: bool = False) -> str:
    if tenant == "tenant-b":
        # B사 운영자가 직접 잘못 배포한 상황. 새 ReplicaSet이 생겨 배포 이력(롤백 대상)이 된다
        kubectl("-n", "tenant-b", "set", "env", f"deploy/{SERVICE[tenant]}", f"DEMO_POOL_SIZE={size}")
        return f"B사 reservation-api에 DEMO_POOL_SIZE={size} 배포"
    return commit_tracked(POOL_FILE, r'(name: DEMO_POOL_SIZE\s*\n\s*value: ")\d+(")', size,
                          f"Tune order-api DB connection pool to {size}", allow_main)


# ── 자동 확장 최대치 (A사 HPA) ──────────────────────────────────


def hpa_max() -> int | None:
    value = kubectl("-n", "tenant-a", "get", "hpa", SERVICE["tenant-a"], "-o", "jsonpath={.spec.maxReplicas}",
                    check=False)
    return int(value) if value.isdigit() else None


def set_hpa_max(size: int, allow_main: bool = False) -> str:
    return commit_tracked(HPA_FILE, r"(maxReplicas: )\d+(\b)", size,
                          f"Restore order-api autoscaler max to {size}", allow_main)


# ── 진입점 ────────────────────────────────────────────────────


def inject(tenant: str, kind: str, size: int = 5, allow_main: bool = False, rate: int | None = None) -> str:
    if "+" in kind:   # 겹친 장애: 하나씩 차례로 더한다
        parts = kind.split("+")
        # 잘못된 배포(pool)는 새 파드를 띄운다. 새 파드가 의존 장애를 이어받게 ConfigMap부터 적는다
        deps = {FAULTS[p] for p in parts if FAULTS.get(p) in DEPENDENCY_FAULTS}
        if deps:
            set_dependency_faults(tenant, dependency_faults(tenant) | deps)
        return "; ".join(inject(tenant, part, size, allow_main, rate) for part in parts)
    if kind == "pool":
        return set_pool(tenant, size, allow_main)
    if kind in FAULTS:
        if FAULTS[kind] in DEPENDENCY_FAULTS:   # 재시작한 새 파드도 이어받게 먼저 적는다
            set_dependency_faults(tenant, dependency_faults(tenant) | {FAULTS[kind]})
        set_fault(tenant, FAULTS[kind])
        return f"{tenant} {SERVICE[tenant]}에 {FAULTS[kind]} 주입"
    if kind == "surge":
        set_surge(tenant, True, rate)
        return f"{tenant} 부하 발생기 켬 (초당 {rate or LOADGEN_RATE[tenant]}건)"
    return clear(tenant, allow_main)


def clear(tenant: str, allow_main: bool = False) -> str:
    """주입한 장애를 모두 뺀다. 풀 크기와 A사 HPA 최대치는 클러스터 값이 평소와 다를 때만 되돌린다."""
    notes = []
    set_dependency_faults(tenant, set())
    set_fault(tenant, None)
    set_surge(tenant, False)
    restores = []
    if pool_size(tenant) not in (None, POOL_NORMAL):
        restores.append(("풀 크기", lambda: set_pool(tenant, POOL_NORMAL, allow_main)))
    if tenant == "tenant-a" and hpa_max() not in (None, HPA_MAX_NORMAL):
        restores.append(("HPA 최대치", lambda: set_hpa_max(HPA_MAX_NORMAL, allow_main)))
    for name, restore in restores:
        try:
            notes.append(restore())
        except SystemExit as e:
            notes.append(f"{name} 복원 안 함: {e}")
    return f"{tenant} 장애 해제" + (f" ({'; '.join(notes)})" if notes else "")


def kind_arg(value: str) -> str:
    parts = value.split("+")
    if any(p not in KINDS for p in parts) or (len(parts) > 1 and "clear" in parts):
        raise argparse.ArgumentTypeError(f"모르는 종류: {value}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tenant", choices=list(SERVICE))
    parser.add_argument("kind", type=kind_arg, help=f"{', '.join(KINDS)}. clear를 뺀 종류는 +로 겹친다 (hang+latency)")
    parser.add_argument("--size", type=int, default=5, help="pool의 풀 크기 (기본 5, 정상은 20)")
    parser.add_argument("--rate", type=int, help="surge의 초당 건수 (기본 A사 250, B사 120)")
    parser.add_argument("--allow-main", action="store_true", help="A사 pool 커밋을 main에 남기는 것을 허용한다")
    args = parser.parse_args()
    print(inject(args.tenant, args.kind, args.size, args.allow_main, args.rate))
    return 0


if __name__ == "__main__":
    sys.exit(main())

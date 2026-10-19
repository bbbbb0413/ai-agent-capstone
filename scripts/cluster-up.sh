#!/usr/bin/env bash
# 고객사 환경(kind + ArgoCD)을 띄운다. compose 스택이 먼저 떠 있어야 한다.
# 비밀값은 .env의 SERVICE_TOKEN을 그대로 쓰고 화면에 출력하지 않는다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=opslens-tenants
NODE="${CLUSTER}-control-plane"
NETWORK=opslens_default
IMAGE=opslens/demo-app:0.1.0
ARGOCD_VERSION=v3.5.5
CTX="kind-${CLUSTER}"
k() { kubectl --context "$CTX" "$@"; }

docker network inspect "$NETWORK" > /dev/null \
  || { echo "compose 네트워크 $NETWORK 가 없다. docker compose up -d 를 먼저 실행한다." >&2; exit 1; }

# 1. 클러스터
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config "$ROOT/gitops/cluster/kind-config.yaml"
fi
# compose 네트워크에도 연결: backend → API 서버·NodePort, 파드 → loki·prometheus
docker network connect "$NETWORK" "$NODE" 2> /dev/null || true

# 2. 고객사 서비스 이미지 (레지스트리 없이 노드에 직접 올린다)
docker build -q -t "$IMAGE" "$ROOT/server/spring/demo-app" > /dev/null
kind load docker-image "$IMAGE" --name "$CLUSTER"

# 3. 네임스페이스·권한·관측 수집기
k apply -f "$ROOT/gitops/cluster/tenants.yaml"
k apply -f "$ROOT/gitops/cluster/metrics-agent.yaml"

# 고객사 서비스 관리 API의 토큰 (compose의 SERVICE_TOKEN과 같은 값)
set -a; . "$ROOT/.env"; set +a
for ns in tenant-a tenant-b; do
  k -n "$ns" create secret generic demo-app-admin --from-literal=token="$SERVICE_TOKEN" \
    --dry-run=client -o yaml | k apply -f - > /dev/null
done

# 4. B사: 처음 한 번 직접 배포 (이후 조치는 OpsLens가 API로)
k apply -k "$ROOT/gitops/tenants/tenant-b"

# 5. ArgoCD와 A사 Application
k create namespace argocd --dry-run=client -o yaml | k apply -f - > /dev/null
k -n argocd apply --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" > /dev/null
k -n argocd rollout status deploy/argocd-repo-server --timeout=180s
k -n argocd rollout status statefulset/argocd-application-controller --timeout=180s
# Git을 30초마다 확인한다 (기본 3분). PR 병합 후 반영까지 기다리는 시간을 줄인다
k -n argocd patch configmap argocd-cm --type merge -p '{"data":{"timeout.reconciliation":"30s"}}' > /dev/null
k -n argocd rollout restart deploy/argocd-repo-server statefulset/argocd-application-controller > /dev/null 2>&1 || true
# 동기화 완료 알림 (backend /internal/gitops/sync-events)
k -n argocd create secret generic argocd-notifications-secret --from-literal=opslens-token="$SERVICE_TOKEN" \
  --dry-run=client -o yaml | k apply -f - > /dev/null
k apply -f "$ROOT/gitops/cluster/argocd-notifications.yaml"
k apply -f "$ROOT/gitops/cluster/argocd-tenant-a.yaml"

# 6. backend가 쓸 고객사별 접속 정보 (git에 올리지 않는다)
mkdir -p "$ROOT/.local/tenants"
for ns in tenant-a tenant-b; do
  k -n "$ns" get secret opslens-operator-token -o jsonpath='{.data.token}' | base64 -d \
    > "$ROOT/.local/tenants/${ns}.token"
done
k config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d \
  > "$ROOT/.local/tenants/ca.crt"
echo "https://${NODE}:6443" > "$ROOT/.local/tenants/api-server"

echo "완료. 상태 확인: kubectl --context $CTX get pods -A"

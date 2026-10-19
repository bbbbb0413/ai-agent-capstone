#!/usr/bin/env bash
# 시나리오 A: A사(GITOPS)에 잘못된 설정을 배포한다.
# 설정 저장소 경로의 커넥션 풀 크기를 5로 줄이는 커밋을 main에 푸시한다 → ArgoCD 반영 → 에러율 급증.
# 이 커밋은 저장소 기록에 남는다. 회복은 OpsLens가 만든 되돌리기 PR을 병합해서 한다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FILE=gitops/tenants/tenant-a/order-api.yaml
SIZE="${1:-5}"

cd "$ROOT"
git diff --quiet -- "$FILE" || { echo "$FILE 에 커밋하지 않은 변경이 있다" >&2; exit 1; }
git pull -q --ff-only
sed -i.bak -E "/name: DEMO_POOL_SIZE/{n;s/value: \"[0-9]+\"/value: \"$SIZE\"/;}" "$FILE" && rm -f "$FILE.bak"
git diff --quiet -- "$FILE" && { echo "이미 풀 크기가 $SIZE 다" >&2; exit 1; }
git commit -q -m "Tune order-api DB connection pool to $SIZE" -- "$FILE"
git push -q
echo "푸시 완료: $(git log --oneline -1). ArgoCD가 30초 안에 반영한다"

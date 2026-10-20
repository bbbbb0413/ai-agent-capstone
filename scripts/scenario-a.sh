#!/usr/bin/env bash
# 시나리오 A: A사(GITOPS)에 잘못된 설정(커넥션 풀 크기 5)을 배포한다. inject.sh의 래퍼다.
# 커밋은 ArgoCD가 따라가는 브랜치에 한다. 그 브랜치가 main이면 --allow-main을 줘야 한다 (main 기록에 남는다).
#   scripts/scenario-a.sh [--allow-main]
exec "$(dirname "$0")/inject.sh" tenant-a pool "$@"

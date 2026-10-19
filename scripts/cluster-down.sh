#!/usr/bin/env bash
# 고객사 환경 클러스터를 지운다. compose 스택은 건드리지 않는다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
kind delete cluster --name opslens-tenants
rm -rf "$ROOT/.local/tenants"

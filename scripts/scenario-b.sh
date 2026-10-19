#!/usr/bin/env bash
# 시나리오 B·B2: B사(DIRECT) reservation-api에 장애를 주입한다.
#   scripts/scenario-b.sh          작업 스레드 고갈(파드 멈춤) → 파드 재시작으로 회복
#   scripts/scenario-b.sh latency  외부 결제 API 지연 → MSP 조치 불가, 고객 공지
# 주입 API는 compose 네트워크 안에서만 닿으므로 backend 컨테이너에서 호출한다 (서비스 토큰은 컨테이너 환경변수).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-hang}" in
  hang) FAULT=WORKER_HANG ;;
  latency) FAULT=PAYMENT_LATENCY ;;
  *) echo "사용법: $0 [hang|latency]" >&2; exit 1 ;;
esac
docker compose -f "$ROOT/docker-compose.yml" exec -T backend sh -c \
  "curl -sf -X POST -H \"Authorization: Bearer \$SERVICE_TOKEN\" -H 'Content-Type: application/json' \
   -d '{\"fault\":\"$FAULT\"}' http://opslens-tenants-control-plane:30082/admin/faults"
echo

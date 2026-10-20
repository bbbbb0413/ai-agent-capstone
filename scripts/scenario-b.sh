#!/usr/bin/env bash
# 시나리오 B·B2: B사(DIRECT) reservation-api에 장애를 주입한다. inject.sh의 래퍼다.
#   scripts/scenario-b.sh          작업 스레드 고갈(파드 멈춤) → 파드 재시작으로 회복
#   scripts/scenario-b.sh latency  외부 결제 API 지연 → MSP 조치 불가, 고객 공지
exec "$(dirname "$0")/inject.sh" tenant-b "${1:-hang}"

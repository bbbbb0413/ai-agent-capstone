#!/usr/bin/env bash
# 고객사 서비스 장애 주입·해제 (SPEC-fault-based-actions 5.7). 본체는 inject.py다.
#   scripts/inject.sh <tenant-a|tenant-b> <pool|hang|latency|dbslow|surge|clear> [--size N] [--allow-main]
exec python3 "$(dirname "$0")/inject.py" "$@"

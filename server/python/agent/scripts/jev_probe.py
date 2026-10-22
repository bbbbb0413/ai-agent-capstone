"""실제 키로 알려진 장애 형태를 Jev에 넣어 기대 조항을 맞히는지 본다 (docs/기능별-변경사항.md T14 실측).

cd server/python/agent && uv run --with typesafe-sdk python scripts/jev_probe.py [--repeat N]
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from agent import jev

ENV_FILE = Path(__file__).resolve().parents[4] / ".env"

CASES = [
    ("A사 커넥션 풀 축소", "RB-STD-01",
     {"metrics": {"error_rate": 0.46, "p95_seconds": 0.31, "requests_per_second": 9.8},
      "logs": {"connection_pool_exhausted": 48}, "recent_change": True}),
    ("B사 파드 멈춤", "RB-STD-02",
     {"metrics": {"error_rate": 0.52, "p95_seconds": 2.1, "requests_per_second": 9.5},
      "logs": {"worker_pool_saturated": 37}, "recent_change": False}),
    ("트래픽 급증", "RB-STD-03",
     {"metrics": {"error_rate": 0.0, "p95_seconds": 2.6, "requests_per_second": 42.0},
      "logs": {"request_queue_backlog": 30}, "recent_change": False}),
    ("외부 결제 API 지연", "RB-STD-04",
     {"metrics": {"error_rate": 0.0, "p95_seconds": 2.2, "requests_per_second": 9.9},
      "logs": {"external_api_timeout": 25}, "recent_change": False}),
    ("DB 지연", "RB-STD-05",
     {"metrics": {"error_rate": 0.01, "p95_seconds": 1.6, "requests_per_second": 9.7},
      "logs": {"slow_query": 31}, "recent_change": False}),
    ("로그 신호 없는 5xx", "RB-STD-07",
     {"metrics": {"error_rate": 0.31, "p95_seconds": 0.4, "requests_per_second": 9.6},
      "logs": {}, "recent_change": False}),
    ("커넥션 풀 고갈 + DB 지연", "RB-STD-05",
     {"metrics": {"error_rate": 0.22, "p95_seconds": 1.8, "requests_per_second": 9.4},
      "logs": {"connection_pool_exhausted": 20, "slow_query": 26}, "recent_change": False}),
]


def load_key() -> bool:
    if os.getenv("TYPESAFE_API_KEY"):
        return True
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                os.environ["TYPESAFE_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
    return bool(os.getenv("TYPESAFE_API_KEY"))


def signal_clause(signals: dict) -> str:
    try:
        from agent.graph import _signal_clause
    except Exception:
        return "?"
    return _signal_clause(signals) or "-"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1, help="같은 입력을 몇 번 물어 판정이 흔들리는지 본다")
    args = parser.parse_args()
    if not load_key():
        print("TYPESAFE_API_KEY가 환경변수와 .env에 없어 건너뛴다")
        return 0

    hits = total = 0
    print(f"{'장애':<20} {'기대':<10} {'Jev':<10} {'확신도':>6} {'시간':>6}  {'코드 규칙':<10} 2순위")
    for name, expected, signals in CASES:
        for _ in range(args.repeat):
            started = time.monotonic()
            result = await jev.classify_fault(signals, timeout=15)
            elapsed = time.monotonic() - started
            total += 1
            if result is None:
                print(f"{name:<20} {expected:<10} {'실패':<10} {'':>6} {elapsed:5.1f}s  {signal_clause(signals):<10}")
                continue
            hits += result["choice"] == expected
            ranked = sorted(result["probabilities"].items(), key=lambda kv: -kv[1])
            second = f"{ranked[1][0]} {ranked[1][1]:.2f}" if len(ranked) > 1 else ""
            mark = "" if result["choice"] == expected else " ✗"
            print(f"{name:<20} {expected:<10} {result['choice'] + mark:<10} {result['confidence']:>6.2f} "
                  f"{elapsed:5.1f}s  {signal_clause(signals):<10} {second}")
    print(f"\n맞힘 {hits}/{total}")
    return 0 if hits == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

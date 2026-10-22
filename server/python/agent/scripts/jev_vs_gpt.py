"""같은 장애 입력과 같은 선택지로 Jev와 GPT의 판정, 시간, 토큰, 비용을 비교한다 (docs/jev.md).

cd server/python/agent && uv run --with typesafe-sdk python scripts/jev_vs_gpt.py [--repeat N] [--models gpt-5-mini,gpt-5-nano]
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from typesafe_sdk import AsyncTypeSafeClient

from agent import jev
from jev_probe import CASES, ENV_FILE

# USD per 1M tokens (2026-10-22 공식 가격표). GPT의 추론 토큰은 출력 토큰으로 과금된다
PRICES = {
    "jev": (0.042, 0.0),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}


class FaultClause(BaseModel):
    clause: Literal["RB-STD-01", "RB-STD-02", "RB-STD-03", "RB-STD-04", "RB-STD-05", "RB-STD-07"]


def load_env() -> None:
    for line in ENV_FILE.read_text().splitlines():
        key, _, value = line.partition("=")
        if key in ("TYPESAFE_API_KEY", "OPENAI_API_KEY") and not os.getenv(key):
            os.environ[key] = value.strip().strip("'\"")


def gpt_prompt(state: dict) -> list:
    criteria = "\n".join(f"- {k}: {v}" for k, v in jev.FAULT_CRITERIA.items())
    return [SystemMessage(f"{jev.FAULT_QUESTION['instructions']}.\n\n선택지:\n{criteria}"),
            HumanMessage(f"현재 상태:\n{json.dumps(state, ensure_ascii=False, indent=1)}")]


async def ask_jev(client: AsyncTypeSafeClient, state: dict) -> dict:
    started = time.monotonic()
    res = await client.system_one(state=state, questions={"fault": jev.FAULT_QUESTION})
    return {"choice": res.answers["fault"].choice, "seconds": time.monotonic() - started,
            "input": res.usage.input_tokens, "output": res.usage.output_tokens, "reasoning": 0}


async def ask_gpt(model: ChatOpenAI, state: dict) -> dict:
    started = time.monotonic()
    out = await model.ainvoke(gpt_prompt(state))
    usage = out["raw"].usage_metadata or {}
    parsed = out["parsed"]
    return {"choice": parsed.clause if parsed else "파싱 실패", "seconds": time.monotonic() - started,
            "input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
            "reasoning": (usage.get("output_token_details") or {}).get("reasoning", 0)}


def cost(name: str, row: dict) -> float:
    price_in, price_out = PRICES[name]
    return (row["input"] * price_in + row["output"] * price_out) / 1_000_000


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--models", default="gpt-5-mini,gpt-5-nano")
    parser.add_argument("--effort", default=os.getenv("LLM_REASONING_EFFORT", "low"),
                        help="GPT reasoning_effort. Agent 기본값과 같은 low")
    args = parser.parse_args()
    load_env()

    gpts = {name: ChatOpenAI(model=name, reasoning_effort=args.effort, timeout=120, max_retries=0)
            .with_structured_output(FaultClause, include_raw=True) for name in args.models.split(",")}
    rows: dict[str, list[dict]] = {"jev": [], **{name: [] for name in gpts}}

    async with AsyncTypeSafeClient(model=jev.JEV_MODEL, timeout=30) as client:
        for case, expected, signals in CASES:
            state = jev.build_state(signals)
            for _ in range(args.repeat):
                for name in rows:
                    try:
                        row = await (ask_jev(client, state) if name == "jev" else ask_gpt(gpts[name], state))
                    except Exception as exc:
                        row = {"choice": f"오류 {type(exc).__name__}", "seconds": 0, "input": 0, "output": 0,
                               "reasoning": 0}
                    row.update(case=case, expected=expected, ok=row["choice"] == expected, cost=cost(name, row))
                    rows[name].append(row)
                    print(f"{name:<11} {case:<18} 기대 {expected} → {row['choice']:<10} "
                          f"{row['seconds']:5.2f}s  in {row['input']:>4} out {row['output']:>4} "
                          f"(추론 {row['reasoning']:>4})", flush=True)

    print("\n| 모델 | 정답 | 평균 시간 | 중앙값 | 최대 | 평균 입력 토큰 | 평균 출력 토큰(추론) | 1회 비용(USD) |")
    print("|---|---|---|---|---|---|---|---|")
    for name, items in rows.items():
        secs = [r["seconds"] for r in items]
        n = len(items)
        print(f"| {name} | {sum(r['ok'] for r in items)}/{n} | {statistics.mean(secs):.2f}s | "
              f"{statistics.median(secs):.2f}s | {max(secs):.2f}s | "
              f"{statistics.mean(r['input'] for r in items):.0f} | "
              f"{statistics.mean(r['output'] for r in items):.0f} "
              f"({statistics.mean(r['reasoning'] for r in items):.0f}) | "
              f"{statistics.mean(r['cost'] for r in items):.7f} |")
    wrong = [(name, r["case"], r["choice"]) for name, items in rows.items() for r in items if not r["ok"]]
    for name, case, choice in wrong:
        print(f"틀림: {name} {case} → {choice}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

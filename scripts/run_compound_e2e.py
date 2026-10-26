#!/usr/bin/env python3
"""Run the existing compound E2E scenarios with explicit ports and Compose files.

Only environment routing is adapted. Scenario, approval, assertion, branch and
cleanup behavior remain in e2e.py and inject.py. Requires a running test stack.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import e2e
import inject

SCENARIOS = ("b-hang-latency", "a-dbslow-backlog", "b-pool-dbslow")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8080")
    parser.add_argument("--web-url", default="http://localhost")
    parser.add_argument("--compose-project", default="opslens")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--compose-file", action="append", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--serial", action="store_true")
    args = parser.parse_args()

    files = args.compose_file or [e2e.ROOT / "docker-compose.yml"]
    for path in [*files, *([args.env_file] if args.env_file else [])]:
        if not path.is_file():
            parser.error(f"file does not exist: {path}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    base = ["docker", "compose", "--project-name", args.compose_project]
    if args.env_file:
        base += ["--env-file", str(args.env_file.resolve())]
    for path in files:
        base += ["-f", str(path.resolve())]

    def compose(*parts: str, check: bool = True) -> str:
        completed = subprocess.run(base + list(parts), cwd=e2e.ROOT,
                                   capture_output=True, text=True)
        if check and completed.returncode:
            raise RuntimeError(f"Compose operation failed (exit {completed.returncode})")
        return completed.stdout.strip()

    def in_backend(script: str, input: str | None = None) -> str:
        completed = subprocess.run(base + ["exec", "-T", "backend", "sh", "-c", script],
                                   cwd=e2e.ROOT, input=input, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(f"Backend command failed (exit {completed.returncode})")
        return completed.stdout.strip()

    e2e.API = args.api_url.rstrip("/")
    e2e.WEB = args.web_url.rstrip("/")
    e2e.compose = compose
    inject.in_backend = in_backend
    sys.argv = ["e2e.py", *SCENARIOS, "--timeout", str(args.timeout)]
    if args.serial:
        sys.argv.append("--serial")
    return e2e.main()


if __name__ == "__main__":
    raise SystemExit(main())

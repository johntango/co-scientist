"""Run or resume the MCP research coordinator from the repository root."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.experiment_agent import clean, run_from_config  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", default="mcp_servers.json")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--max-experiments", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=120)
    parser.add_argument("--max-calls", type=int, default=32)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_from_config(
                args.question,
                args.model,
                args.config,
                session_id=args.resume,
                max_experiments=args.max_experiments,
                max_runs=args.max_runs,
                max_calls=args.max_calls,
            )
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "completed" else 1
    except Exception as exc:
        print(json.dumps({"error": clean(str(exc))}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI with JSON stdout and nonzero exit codes for invalid/failed/incomplete work."""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from .environment import describe
from .models import Experiment
from .runner import Limits, Runner, artifact_dir, artifacts, results
from .store import Store


def emit(value):
    print(json.dumps(value, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(prog="teamlab")
    parser.add_argument("--data-dir", default=None, help="Local data directory (or TEAMLAB_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe")
    for name in ("validate", "run", "compare", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("config")
    for name in ("status", "results", "export"):
        p = sub.add_parser(name)
        p.add_argument("id")
        if name == "results":
            p.add_argument("--format", choices=["json"], default="json")
        if name == "export":
            p.add_argument("--output", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        if args.command == "describe":
            emit(describe(Limits()))
        elif args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
        elif args.command in ("validate", "run", "compare", "sweep"):
            config = Experiment.model_validate_json(Path(args.config).read_text())
            required = {"run": "single", "compare": "comparison", "sweep": "sweep"}.get(args.command)
            if required and config.mode != required:
                raise ValueError(f"{args.command} requires mode={required}")
            if args.command == "validate":
                # Validate limits without acquiring executor ownership or interrupting jobs.
                runner = object.__new__(Runner)
                runner.store, runner.limits = Store(args.data_dir), Limits()
                runner.validate(config)
                emit({"valid": True, "config": config.model_dump()})
                return 0
            runner = Runner(Store(args.data_dir))
            try:
                item = runner.submit(config)
                while True:
                    state = runner.store.experiment(item["id"])
                    if state["status"] not in ("queued", "running", "cancelling"):
                        break
                    time.sleep(0.05)
                emit(state)
                return 0 if state["status"] == "completed" else 3
            finally:
                runner.close()
        else:
            store = Store(args.data_dir)
            if args.command == "status":
                emit(store.experiment(args.id))
            elif args.command == "results":
                emit(results(store, args.id))
            else:
                root = artifact_dir(store, args.id)
                entries = artifacts(store, args.id)
                if not entries:
                    raise ValueError("No artifacts available")
                output = Path(args.output)
                output.mkdir(parents=True, exist_ok=True)
                for item in entries:
                    if item["name"].startswith("workload-"):
                        source = store.root / "workloads" / item["name"].removeprefix("workload-")
                    else:
                        source = root / item["name"]
                    shutil.copy2(source, output / item["name"])
                emit({"output": str(output.resolve()), "files": [a["name"] for a in entries]})
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"error": {"code": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

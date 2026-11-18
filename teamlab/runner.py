"""Bounded executor shared by CLI and HTTP, with durable state and cooperative cancel."""

import csv
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .engine import simulate
from .models import variants
from .statistics import compare
from .store import Store, timestamp
from .workload import Cancelled, LimitExceeded, canonical, generate


class BusyError(ValueError):
    pass


class Limits:
    def __init__(self):
        self.max_jobs = int(os.getenv("TEAMLAB_MAX_JOBS", "20000"))
        self.max_replications = int(os.getenv("TEAMLAB_MAX_REPLICATIONS", "30"))
        self.max_sweep_cells = int(os.getenv("TEAMLAB_MAX_SWEEP_CELLS", "25"))
        self.max_job_evaluations = int(os.getenv("TEAMLAB_MAX_JOB_EVALUATIONS", "500000"))
        self.max_runs = int(os.getenv("TEAMLAB_MAX_RUNS", "400"))
        self.max_concurrent = int(os.getenv("TEAMLAB_MAX_CONCURRENT", "2"))
        self.max_pending = int(os.getenv("TEAMLAB_MAX_PENDING", "8"))
        self.max_seconds = int(os.getenv("TEAMLAB_MAX_SECONDS", "300"))
        if min(vars(self).values()) < 1:
            raise ValueError("All execution limits must be positive")


def provenance():
    files = sorted(Path(__file__).parent.rglob("*.py")) + sorted((Path(__file__).parent / "static").glob("*"))
    source_hash = hashlib.sha256(
        b"".join(p.relative_to(Path(__file__).parent).as_posix().encode() + p.read_bytes() for p in files)
    ).hexdigest()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {
        "source_hash": source_hash,
        "git_revision": revision,
        "dependencies": {n: importlib.metadata.version(n) for n in ("simpy", "pydantic", "fastapi", "uvicorn")},
        "policy_version": "1.0.0",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def artifact_dir(store, ident):
    store.experiment(ident)  # Validate against SQLite before using an identifier as a path.
    return store.root / "experiments" / ident


def save_artifacts(store, ident, config, result):
    root = artifact_dir(store, ident)
    (root / "config.json").write_text(canonical(config.model_dump()))
    (root / "results.json").write_text(canonical(result))
    (root / "metadata.json").write_text(canonical(store.experiment(ident)))
    fields = [
        "cell",
        "seed",
        "policy",
        "workload_id",
        "status",
        "mean_wait",
        "median_wait",
        "p95_wait",
        "mean_turnaround",
        "completion_fraction",
        "throughput",
        "utilization",
        "mean_queue_length",
        "setup_minutes",
        "processing_minutes",
    ]
    with (root / "metrics.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for cell in result["cells"]:
            for run in cell["runs"]:
                row = {"cell": cell["parameters"], **run, **run["cohort"], **run["window"]}
                writer.writerow({key: row.get(key) for key in fields})
    with (root / "paired.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["cell", "baseline", "policy", "metric", "mean_difference", "ci95_low", "ci95_high", "n", "missing_pairs"]
        )
        for cell in result["cells"]:
            for pair in cell["comparisons"]:
                writer.writerow(
                    [
                        canonical(cell["parameters"]),
                        pair["baseline"],
                        pair["policy"],
                        pair["metric"],
                        pair["mean"],
                        *(pair["ci95"] or [None, None]),
                        pair["n"],
                        pair["missing_pairs"],
                    ]
                )
    if config.detailed_logs:
        with (root / "jobs.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            fields = [
                "id",
                "arrival",
                "task",
                "duration",
                "assignment",
                "start",
                "completion",
                "worker",
                "setup",
                "setup_elapsed",
                "queue_delay",
                "waiting",
                "turnaround",
            ]
            writer.writerow(["cell", "seed", "policy", *fields])
            for cell in result["cells"]:
                for run in cell["runs"]:
                    for j in run["jobs"]:
                        writer.writerow(
                            [canonical(cell["parameters"]), run["seed"], run["policy"], *(j[k] for k in fields)]
                        )
    if config.research_record_id:
        record = next(r for r in store.records() if r["id"] == config.research_record_id)
        (root / "research-record.json").write_text(canonical(record))
    # Portable standalone SVG: all replication mean waits, no implied ranking narrative.
    points = [
        (f"cell {i + 1} / {r['policy']} / seed {r['seed']}", r["cohort"]["mean_wait"])
        for i, c in enumerate(result["cells"])
        for r in c["runs"]
    ]
    maximum = max([v for _, v in points if v is not None] or [1]) or 1
    import html

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="%s" role="img">' % (60 + 24 * len(points)),
        '<rect width="100%" height="100%" fill="#f5f7fa"/>',
        '<text x="20" y="25" font-family="sans-serif">Cohort mean waiting time (minutes); each row is an independent-workload policy run</text>',
    ]
    for i, (label, value) in enumerate(points):
        y = 55 + 24 * i
        lines.append(f'<text x="15" y="{y}" font-size="12" font-family="sans-serif">{html.escape(label)}</text>')
        if value is not None:
            lines.append(
                f'<rect x="430" y="{y - 12}" width="{450 * value / maximum}" height="14" fill="#296e79"/><text x="900" y="{y}" font-size="12">{value:.3f}</text>'
            )
        else:
            lines.append(
                f'<text x="430" y="{y}" font-size="12" fill="#b22">undefined / incomplete or empty cohort</text>'
            )
    (root / "waiting.svg").write_text("".join(lines) + "</svg>")


class Runner:
    def __init__(self, store=None, limits=None):
        self.store = store or Store()
        self.limits = limits or Limits()
        # One executor owns a data directory. Read-only CLI commands need no lock.
        self.lockfile = (self.store.root / "executor.lock").open("a+")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise BusyError(
                "An executor already owns this data directory. Use its HTTP API, stop it, or select another TEAMLAB_DATA_DIR."
            ) from None
        self.store.interrupted()
        self.pool = ThreadPoolExecutor(max_workers=self.limits.max_concurrent, thread_name_prefix="teamlab")
        self.guard = threading.RLock()
        self.pending = {}

    def validate(self, config):
        cells = list(variants(config))
        if (
            len(config.seeds) > self.limits.max_replications
            or len(cells) > self.limits.max_sweep_cells
            or len(cells) * len(config.seeds) * len(config.policies) > self.limits.max_runs
        ):
            raise LimitExceeded("Experiment exceeds replication, sweep-cell, or total-run limit")
        if config.research_record_id and not any(r["id"] == config.research_record_id for r in self.store.records()):
            raise KeyError("Unknown research record")
        if config.workload_id:
            w = self.store.workload(config.workload_id)
            if w["job_count"] > self.limits.max_jobs:
                raise LimitExceeded("Persisted workload exceeds max_jobs")
            if config.workload.model_dump() != w["config"] or config.seeds != [w["seed"]]:
                raise ValueError("For workload_id, workload config and seed must exactly match its metadata")
        return cells

    def submit(self, config, request_key=None):
        cells = self.validate(config)
        with self.guard:
            if request_key:
                previous = self.store.submission(request_key, config.model_dump())
                if previous:
                    return previous
            if len(self.pending) >= self.limits.max_pending:
                raise BusyError("Experiment queue is full")
            entry = self.store.create_experiment(config.model_dump(), provenance(), request_key)
            root = artifact_dir(self.store, entry["id"])
            (root / "config.json").write_text(canonical(config.model_dump()))
            event = threading.Event()
            self.pending[entry["id"]] = event
            self.pool.submit(self.execute, entry["id"], config, cells, event)
        return entry

    def cancel(self, ident):
        with self.guard:
            entry = self.store.experiment(ident)
            if ident in self.pending and entry["status"] in ("queued", "running", "cancelling"):
                self.pending[ident].set()
                entry = self.store.update(ident, status="cancelling")
            return entry

    def execute(self, ident, config, cells, event):
        import time

        start = time.monotonic()

        def stopped():
            if time.monotonic() - start > self.limits.max_seconds:
                raise LimitExceeded("Experiment exceeded wall-clock execution limit")
            return event.is_set()

        total = len(cells) * len(config.seeds) * len(config.policies)
        done = 0
        job_evaluations = 0
        result = {"experiment_id": ident, "exploratory": config.mode == "sweep", "cells": [], "warnings": []}
        if config.mode == "sweep":
            result["warnings"].append(
                "Exploratory sweep: confidence intervals are pointwise, without multiplicity correction; no family-wide significance claims."
            )
        try:
            if stopped():
                raise Cancelled()
            self.store.update(
                ident, status="running", started_at=timestamp(), progress={"completed": 0, "total": total}
            )
            for params, variant in cells:
                runs = []
                for seed in config.seeds:
                    if stopped():
                        raise Cancelled()
                    workload = (
                        self.store.workload(config.workload_id, full=True)
                        if config.workload_id
                        else generate(variant.workload, seed, self.limits.max_jobs, stopped)
                    )
                    job_evaluations += len(workload["jobs"]) * len(config.policies)
                    if job_evaluations > self.limits.max_job_evaluations:
                        raise LimitExceeded(
                            "Total job-policy evaluation budget exceeded; reduce replications, sweep size, or horizon"
                        )
                    self.store.save_workload(workload)
                    for policy in config.policies:
                        p = next(p for p in variant.policies if p.name == policy.name)
                        runs.append(simulate(workload, variant, p, stopped))
                        done += 1
                        self.store.update(ident, progress={"completed": done, "total": total})
                if stopped():
                    raise Cancelled()
                result["cells"].append(
                    {
                        "parameters": params,
                        "runs": runs,
                        "comparisons": compare(
                            runs, [p.name for p in config.policies], config.bootstrap_seed, config.bootstrap_samples
                        ),
                    }
                )
            warnings = sorted(
                {w for c in result["cells"] for r in c["runs"] for w in r["warnings"]} | set(result["warnings"])
            )
            result["warnings"] = warnings
            with self.guard:
                if stopped():
                    raise Cancelled()
                save_artifacts(self.store, ident, config, result)
                incomplete = any(r["status"] == "incomplete" for c in result["cells"] for r in c["runs"])
                self.store.update(
                    ident,
                    status="incomplete" if incomplete else "completed",
                    finished_at=timestamp(),
                    warnings=warnings,
                )
                (artifact_dir(self.store, ident) / "metadata.json").write_text(canonical(self.store.experiment(ident)))
        except Cancelled:
            self.store.update(
                ident,
                status="cancelled",
                finished_at=timestamp(),
                warnings=["Experiment cancelled; partial runs are not published as complete results."],
            )
        except Exception as exc:
            self.store.update(
                ident, status="failed", finished_at=timestamp(), error={"code": type(exc).__name__, "message": str(exc)}
            )
        finally:
            try:
                (artifact_dir(self.store, ident) / "metadata.json").write_text(canonical(self.store.experiment(ident)))
            finally:
                with self.guard:
                    self.pending.pop(ident, None)

    def close(self):
        with self.guard:
            for event in self.pending.values():
                event.set()
        self.pool.shutdown(wait=True)
        fcntl.flock(self.lockfile, fcntl.LOCK_UN)
        self.lockfile.close()


def results(store, ident):
    state = store.experiment(ident)
    if state["status"] not in ("completed", "incomplete"):
        raise ValueError(f"Results unavailable for experiment status {state['status']}")
    return json.loads((artifact_dir(store, ident) / "results.json").read_text())


def artifacts(store, ident):
    root = artifact_dir(store, ident)
    entries = [
        {"name": p.name, "url": f"/api/v1/experiments/{ident}/artifacts/{p.name}"}
        for p in sorted(root.iterdir())
        if p.is_file()
    ]
    if store.experiment(ident)["status"] in ("completed", "incomplete"):
        ids = sorted({r["workload_id"] for c in results(store, ident)["cells"] for r in c["runs"]})
        entries += [{"name": f"workload-{i}.json", "url": f"/api/v1/workloads/{i}/download"} for i in ids]
    return entries

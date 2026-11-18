"""Paired replication-level percentile bootstrap, never a job-level bootstrap."""

import random
from itertools import combinations
from statistics import fmean

from .engine import quantile

METRICS = [
    ("cohort", "mean_wait"),
    ("cohort", "p95_wait"),
    ("cohort", "mean_turnaround"),
    ("window", "throughput"),
    ("window", "utilization"),
    ("window", "mean_queue_length"),
]


def bootstrap(differences, seed, samples):
    if not differences:
        return {"mean": None, "ci95": None, "n": 0}
    mean = fmean(differences)
    if len(differences) < 2:
        return {"mean": mean, "ci95": None, "n": len(differences)}
    rng = random.Random(seed)
    means = [fmean(rng.choices(differences, k=len(differences))) for _ in range(samples)]
    return {"mean": mean, "ci95": [quantile(means, 0.025), quantile(means, 0.975)], "n": len(differences)}


def compare(runs, policies, seed, samples):
    result = []
    by = {(r["seed"], r["policy"]): r for r in runs}
    seeds = sorted({r["seed"] for r in runs})
    for a, b in combinations(policies, 2):
        for section, metric in METRICS:
            pairs = []
            for s in seeds:
                ra, rb = by[s, a], by[s, b]
                if ra["workload_hash"] != rb["workload_hash"]:
                    raise ValueError("Pairing requires identical workload hashes")
                va, vb = ra[section][metric], rb[section][metric]
                pairs.append(
                    {
                        "seed": s,
                        "workload_hash": ra["workload_hash"],
                        "difference": None if va is None or vb is None else vb - va,
                    }
                )
            valid = [p["difference"] for p in pairs if p["difference"] is not None]
            # Do not drop censored replications and manufacture a favorable comparison.
            summary = (
                bootstrap(valid, seed, samples)
                if len(valid) == len(pairs)
                else {"mean": None, "ci95": None, "n": len(valid)}
            )
            result.append(
                {
                    "baseline": a,
                    "policy": b,
                    "metric": f"{section}.{metric}",
                    "direction": f"{b} minus {a}",
                    **summary,
                    "replications": pairs,
                    "missing_pairs": len(pairs) - len(valid),
                    "bootstrap": {
                        "method": "paired replication-level percentile bootstrap",
                        "seed": seed,
                        "samples": samples,
                    },
                    "warning": "CI unavailable with fewer than two replications or any undefined pair"
                    if summary["ci95"] is None
                    else None,
                }
            )
    return result

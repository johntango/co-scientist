"""Central SimPy dispatcher with deterministic batch ordering and clipped metrics."""

import math
from statistics import fmean

import simpy

from .models import Experiment, Policy, WorkloadConfig
from .policies import POLICIES, POLICY_VERSION
from .workload import Cancelled


def quantile(values, p):
    if not values:
        return None
    x = sorted(values)
    pos = (len(x) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return x[lo] + (x[hi] - x[lo]) * (pos - lo)


def overlap(start, end, lo, hi):
    return max(0, min(end, hi) - max(start, lo))


def nominal_load(workload, workers):
    a = workload.arrivals
    ma = workload.service_a.mean
    mb = (workload.service_b or workload.service_a).mean
    if a.process == "poisson":
        type_rates = [a.rate * a.p_a, a.rate * (1 - a.p_a)]
    else:
        q0, q1 = (s.transition_rate for s in a.states)
        weights = [q1 / (q0 + q1), q0 / (q0 + q1)]
        type_rates = [
            sum(
                w * a.rate * s.rate_multiplier * (s.p_a if task == "A" else 1 - s.p_a)
                for w, s in zip(weights, a.states)
            )
            for task in ("A", "B")
        ]
    return {
        "overall": (type_rates[0] * ma + type_rates[1] * mb) / workers,
        "type_work_per_minute": dict(zip(("A", "B"), (type_rates[0] * ma, type_rates[1] * mb))),
        "basis": "Poisson rate, or stationary MMPP state mixture; excludes setup",
    }


def simulate(workload, config: Experiment, policy: Policy, cancelled=lambda: False):
    wc = WorkloadConfig.model_validate(workload["config"])
    lo, hi = wc.warmup, wc.warmup + wc.observation
    cap = hi + config.max_drain
    spec = POLICIES[policy.name]
    workers = [
        {
            "id": w.id,
            "eligible": set(w.eligibility) & ({w.partition} if spec["partitioned"] else {"A", "B"}),
            "current": None,
            "job": None,
        }
        for w in config.workers
    ]
    # Input order breaks equal-arrival ties; IDs remain externally meaningful.
    jobs = [
        {**j, "assignment": None, "start": None, "completion": None, "worker": None, "setup": None}
        for j in workload["jobs"]
    ]
    if len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("Workload job IDs must be unique")
    if any(j["arrival"] < 0 or j["arrival"] >= hi or j["duration"] <= 0 for j in jobs):
        raise ValueError("Jobs must arrive in [0, warmup + observation) and have positive durations")
    if any(not any(j["task"] in w["eligible"] for w in workers) for j in jobs):
        raise ValueError(f"Policy {policy.name} has no eligible worker for a workload task type")
    order = sorted(range(len(jobs)), key=lambda i: (jobs[i]["arrival"], i))
    waiting, trace = [], []
    queue_area = {"A": 0.0, "B": 0.0}
    end_queue = {"A": 0, "B": 0}
    env = simpy.Environment()

    def run():
        nonlocal end_queue
        cursor = 0
        previous = 0.0
        while True:
            if cancelled():
                raise Cancelled()
            now = env.now
            for task in ("A", "B"):
                queue_area[task] += sum(jobs[i]["task"] == task for i in waiting) * overlap(previous, now, lo, hi)
            # Completions first, then all arrivals, then idle workers in configured order.
            for w in workers:
                if w["job"] is not None and jobs[w["job"]]["planned_completion"] <= now:
                    j = jobs[w["job"]]
                    j["completion"] = j["planned_completion"]
                    w["job"] = None
            while cursor < len(order) and jobs[order[cursor]]["arrival"] <= now:
                waiting.append(order[cursor])
                cursor += 1
            for w in workers:
                if w["job"] is not None:
                    continue
                eligible = [i for i in waiting if jobs[i]["task"] in w["eligible"]]
                if not eligible or now >= cap:
                    continue
                chosen = spec["select"]([jobs[i] for i in eligible], w, now, policy.aging_threshold)
                i = eligible[chosen]
                waiting.remove(i)
                j = jobs[i]
                setup = config.setup_delay if w["current"] is not None and w["current"] != j["task"] else 0.0
                j.update(
                    assignment=now,
                    worker=w["id"],
                    setup=setup,
                    planned_start=now + setup,
                    planned_completion=now + setup + j["duration"],
                )
                w.update(job=i, current=j["task"])
            counts = {task: sum(jobs[i]["task"] == task for i in waiting) for task in ("A", "B")}
            trace.append({"time": now, "A": counts["A"], "B": counts["B"], "total": len(waiting)})
            if now == hi:
                end_queue = counts
            if now >= cap or (
                cursor == len(order) and not waiting and all(w["job"] is None for w in workers) and now >= hi
            ):
                break
            times = [cap]
            if cursor < len(order):
                times.append(jobs[order[cursor]]["arrival"])
            times.extend(jobs[w["job"]]["planned_completion"] for w in workers if w["job"] is not None)
            times.extend(t for t in (lo, hi) if t > now)
            previous = now
            yield env.timeout(min(times) - now)

    env.process(run())
    env.run()
    end = env.now
    for j in jobs:
        if j["assignment"] is not None and j["planned_start"] <= end:
            j["start"] = j["planned_start"]
        j["setup_elapsed"] = 0.0 if j["assignment"] is None else overlap(j["assignment"], j["planned_start"], 0, end)
        j["queue_delay"] = None if j["assignment"] is None else j["assignment"] - j["arrival"]
        j["waiting"] = None if j["start"] is None else j["start"] - j["arrival"]
        j["turnaround"] = None if j["completion"] is None else j["completion"] - j["arrival"]
    cohort = [j for j in jobs if lo <= j["arrival"] < hi]

    def cohort_metrics(items):
        completed = sum(j["completion"] is not None for j in items)
        full = completed == len(items)
        waits = [j["waiting"] for j in items] if full else []
        return {
            "arrivals": len(items),
            "completed": completed,
            "unfinished": len(items) - completed,
            "completion_fraction": completed / len(items) if items else None,
            "mean_wait": fmean(waits) if waits else None,
            "median_wait": quantile(waits, 0.5),
            "p95_wait": quantile(waits, 0.95),
            "mean_turnaround": fmean(j["turnaround"] for j in items) if items and full else None,
            "mean_queue_delay": fmean(j["queue_delay"] for j in items) if items and full else None,
            "mean_setup": fmean(j["setup"] for j in items) if items and full else None,
        }

    utilization = []
    per_type_time = {t: {"processing": 0.0, "setup": 0.0} for t in ("A", "B")}
    for w in workers:
        processing, setup = 0.0, 0.0
        for j in jobs:
            if j["worker"] == w["id"]:
                st = overlap(j["assignment"], j["planned_start"], lo, hi)
                pt = overlap(j["planned_start"], j["planned_completion"], lo, hi)
                processing += pt
                setup += st
                per_type_time[j["task"]]["setup"] += st
                per_type_time[j["task"]]["processing"] += pt
        utilization.append(
            {
                "worker": w["id"],
                "processing_minutes": processing,
                "setup_minutes": setup,
                "processing_fraction": processing / wc.observation,
                "setup_fraction": setup / wc.observation,
                "busy_fraction": (processing + setup) / wc.observation,
            }
        )
    window_completed = sum(j["completion"] is not None and lo <= j["completion"] < hi for j in jobs)
    nominal = nominal_load(wc, len(workers))
    unfinished = sum(j["completion"] is None for j in jobs)
    warnings = ["Warm-up alone does not establish steady state; these are finite-horizon results."]
    if nominal["overall"] >= 1:
        warnings.append("Nominal offered load is at least total capacity even before setup; queues may grow.")
    for task in ("A", "B"):
        capacity = sum(task in w["eligible"] for w in workers)
        if nominal["type_work_per_minute"][task] >= capacity and nominal["type_work_per_minute"][task] > 0:
            warnings.append(f"Type {task} nominal load reaches its eligible capacity; queues may grow.")
    if unfinished:
        warnings.append(
            "INCOMPLETE: maximum drain reached. Cohort waiting/turnaround summaries are null if any cohort job is unfinished; no completed-only estimates."
        )
    cm = cohort_metrics(cohort)
    histogram = []
    if cohort and cm["unfinished"] == 0:
        maximum = max(j["waiting"] for j in cohort)
        width = max(maximum / 20, 0.01)
        bins = [0] * 20
        for j in cohort:
            bins[min(19, int(j["waiting"] / width))] += 1
        histogram = [{"left": i * width, "right": (i + 1) * width, "count": n} for i, n in enumerate(bins)]
    result = {
        "policy": policy.name,
        "policy_version": POLICY_VERSION,
        "workload_id": workload["id"],
        "workload_hash": workload["hash"],
        "seed": workload["seed"],
        "status": "incomplete" if unfinished else "complete",
        "simulation_end": end,
        "total_jobs": len(jobs),
        "unfinished_jobs": unfinished,
        "cohort": cm,
        "window": {
            "start": lo,
            "end": hi,
            "completed": window_completed,
            "throughput": window_completed / wc.observation,
            "mean_queue_length": sum(queue_area.values()) / wc.observation,
            "end_queue_length": sum(end_queue.values()),
            "processing_minutes": sum(u["processing_minutes"] for u in utilization),
            "setup_minutes": sum(u["setup_minutes"] for u in utilization),
            "utilization": fmean(u["busy_fraction"] for u in utilization),
        },
        "workers": utilization,
        "nominal_offered_load": nominal,
        "realized_cohort_load": sum(j["duration"] for j in cohort) / wc.observation / len(workers),
        "per_type": {
            task: {
                "cohort": cohort_metrics([j for j in cohort if j["task"] == task]),
                "window": {
                    "throughput": sum(
                        j["task"] == task and j["completion"] is not None and lo <= j["completion"] < hi for j in jobs
                    )
                    / wc.observation,
                    "mean_queue_length": queue_area[task] / wc.observation,
                    "end_queue_length": end_queue[task],
                    **per_type_time[task],
                },
            }
            for task in ("A", "B")
        },
        "warnings": warnings,
        "waiting_histogram": histogram,
        "queue_series": trace,
        "timeline": [{k: v for k, v in j.items() if not k.startswith("planned_")} for j in jobs[:60]],
    }
    if config.detailed_logs:
        result["jobs"] = [{k: v for k, v in j.items() if not k.startswith("planned_")} for j in jobs]
    return result

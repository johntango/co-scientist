"""Policy-independent immutable workload generation with separate RNG streams."""

import hashlib
import json
import math
import random

from .models import Job, WorkloadConfig


class LimitExceeded(ValueError):
    pass


class Cancelled(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stream_seeds(seed):
    return {
        name: int(hashlib.sha256(f"teamlab-v1:{seed}:{name}".encode()).hexdigest()[:16], 16)
        for name in ("arrivals", "types", "durations")
    }


def generate(config: WorkloadConfig, seed: int, max_jobs=100000, cancelled=lambda: False):
    seeds = stream_seeds(seed)
    arrivals, types, durations = (random.Random(seeds[k]) for k in ("arrivals", "types", "durations"))
    horizon = config.warmup + config.observation
    t, state, events = 0.0, config.arrivals.initial_state, 0
    jobs = []
    while t < horizon:
        if cancelled():
            raise Cancelled()
        events += 1
        if events > max(10000, max_jobs * 20):
            raise LimitExceeded("Arrival/state event budget exceeded; reduce horizon or transition rates")
        a = config.arrivals
        s = a.states[state]
        rate = a.rate * (s.rate_multiplier if a.process == "mmpp" else 1)
        transition = s.transition_rate if a.process == "mmpp" else 0
        if rate + transition == 0:
            break
        t += arrivals.expovariate(rate + transition)
        if t >= horizon:
            break
        if transition and arrivals.random() < transition / (rate + transition):
            state = 1 - state
            continue
        if len(jobs) >= max_jobs:
            raise LimitExceeded(f"Workload exceeds max_jobs={max_jobs}; no partial workload was accepted")
        task = "A" if types.random() < (s.p_a if a.process == "mmpp" else a.p_a) else "B"
        spec = config.service_a if task == "A" else config.service_b or config.service_a
        if spec.distribution == "deterministic":
            duration = spec.mean
        elif spec.distribution == "exponential":
            duration = durations.expovariate(1 / spec.mean)
        else:
            sigma = math.sqrt(math.log1p(spec.cv**2))
            duration = durations.lognormvariate(math.log(spec.mean) - sigma**2 / 2, sigma)
        jobs.append(Job(id=f"j{len(jobs):07d}", arrival=t, task=task, duration=duration).model_dump())
    body = {"version": 1, "config": config.model_dump(), "seed": seed, "stream_seeds": seeds, "jobs": jobs}
    return {"id": digest(body), "hash": digest(body), **body}

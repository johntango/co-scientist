"""Validated, bounded scientific inputs. All times and rates use minutes."""

from itertools import product
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Task = Literal["A", "B"]
PolicyName = Literal["specialist_fcfs", "flexible_fcfs", "type_preference", "static_partition"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Service(StrictModel):
    distribution: Literal["deterministic", "exponential", "lognormal"] = "exponential"
    mean: float = Field(default=1, gt=0, le=10000)
    cv: float = Field(default=1, ge=0, le=10)


class BurstState(StrictModel):
    rate_multiplier: float = Field(default=1, ge=0, le=100)
    p_a: float = Field(default=0.5, ge=0, le=1)
    transition_rate: float = Field(default=0.1, gt=0, le=100)


class Arrivals(StrictModel):
    process: Literal["poisson", "mmpp"] = "poisson"
    rate: float = Field(default=2.5, ge=0, le=1000, description="Jobs per minute before state multiplier")
    p_a: float = Field(default=0.5, ge=0, le=1)
    states: list[BurstState] = Field(
        default_factory=lambda: [BurstState(), BurstState(rate_multiplier=2, p_a=0.8)],
        min_length=2,
        max_length=2,
    )
    initial_state: Literal[0, 1] = 0


class WorkloadConfig(StrictModel):
    time_unit: Literal["minutes"] = "minutes"
    warmup: float = Field(default=100, ge=0, le=100000)
    observation: float = Field(default=500, gt=0, le=1000000)
    arrivals: Arrivals = Field(default_factory=Arrivals)
    service_a: Service = Field(default_factory=Service)
    service_b: Service | None = Field(default=None, description="Null uses the same distribution as A")


class Job(StrictModel):
    id: str
    arrival: float = Field(ge=0)
    task: Task
    duration: float = Field(gt=0)


class Worker(StrictModel):
    id: str
    eligibility: list[Task] = Field(default_factory=lambda: ["A", "B"], min_length=1, max_length=2)
    partition: Task


class Policy(StrictModel):
    name: PolicyName
    aging_threshold: float = Field(default=5, ge=0, le=100000)


SWEEP_PATHS = {
    "setup_delay",
    "aging_threshold",
    "workload.arrivals.rate",
    "workload.arrivals.p_a",
    "workload.arrivals.states.0.rate_multiplier",
    "workload.arrivals.states.1.rate_multiplier",
    "workload.arrivals.states.0.p_a",
    "workload.arrivals.states.1.p_a",
    "workload.arrivals.states.0.transition_rate",
    "workload.arrivals.states.1.transition_rate",
    "workload.service_a.cv",
    "workload.service_a.mean",
    "workload.service_b.cv",
    "workload.service_b.mean",
}


class Experiment(StrictModel):
    mode: Literal["single", "comparison", "sweep"] = "comparison"
    workload: WorkloadConfig = Field(default_factory=WorkloadConfig)
    workload_id: str | None = None
    seeds: list[int] = Field(default_factory=lambda: [11, 22, 33, 44, 55], min_length=1, max_length=100)
    policies: list[Policy] = Field(
        default_factory=lambda: [
            Policy(name=n) for n in ("specialist_fcfs", "flexible_fcfs", "type_preference", "static_partition")
        ],
        min_length=1,
        max_length=4,
    )
    workers: list[Worker] = Field(
        default_factory=lambda: [Worker(id=f"w{i + 1}", partition="A" if i < 2 else "B") for i in range(4)],
        min_length=1,
        max_length=64,
    )
    setup_delay: float = Field(default=0, ge=0, le=10000)
    max_drain: float = Field(default=1000, ge=0, le=100000)
    bootstrap_seed: int = Field(default=2026, ge=0, le=2**63 - 1)
    bootstrap_samples: int = Field(default=2000, ge=100, le=10000)
    detailed_logs: bool = True
    research_record_id: str | None = None
    sweep: dict[str, list[float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_design(self):
        if len(set(self.seeds)) != len(self.seeds) or any(s < 0 or s > 2**63 - 1 for s in self.seeds):
            raise ValueError("Seeds must be distinct integers from 0 to 2^63-1")
        if len({p.name for p in self.policies}) != len(self.policies):
            raise ValueError("Policy names must be unique")
        if len({w.id for w in self.workers}) != len(self.workers):
            raise ValueError("Worker IDs must be unique")
        if self.mode == "single" and len(self.policies) != 1:
            raise ValueError("Single mode requires exactly one policy")
        if self.mode in ("comparison", "sweep") and len(self.policies) < 2:
            raise ValueError("Comparison and sweep require at least two policies")
        if bool(self.sweep) != (self.mode == "sweep"):
            raise ValueError("Only sweep mode accepts sweep axes, and requires at least one")
        if len(self.sweep) > 2 or any(k not in SWEEP_PATHS or not v or len(v) > 20 for k, v in self.sweep.items()):
            raise ValueError("Use 1–2 supported sweep axes, each with 1–20 values")
        if any(len(set(v)) != len(v) for v in self.sweep.values()):
            raise ValueError("Sweep axis values must be unique")
        if self.workload_id and (len(self.seeds) != 1 or self.sweep):
            raise ValueError(
                "An existing workload requires one seed and no sweep; its configuration/seed are authoritative"
            )
        return self


def variants(config: Experiment):
    """Expand bounded axes and revalidate every resulting configuration."""
    keys = sorted(config.sweep)  # Canonical row/column order also used in persisted JSON and the heatmap.
    for values in product(*(config.sweep[k] for k in keys)):
        raw = config.model_dump()
        raw.update(mode="comparison" if len(config.policies) > 1 else "single", sweep={})
        params = dict(zip(keys, values))
        for path, value in params.items():
            if path == "aging_threshold":
                for policy in raw["policies"]:
                    policy["aging_threshold"] = value
                continue
            parts = path.split(".")
            node = raw
            if path.startswith("workload.service_b.") and raw["workload"]["service_b"] is None:
                raw["workload"]["service_b"] = dict(raw["workload"]["service_a"])
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            node[parts[-1]] = value
        yield params, Experiment.model_validate(raw)


class WorkloadRequest(StrictModel):
    config: WorkloadConfig = Field(default_factory=WorkloadConfig)
    seed: int = Field(default=11, ge=0, le=2**63 - 1)


class ResearchRecord(StrictModel):
    question: str = Field(min_length=1, max_length=10000)
    hypothesis: str = Field(min_length=1, max_length=10000)
    mechanism: str = Field(min_length=1, max_length=10000)
    quantitative_prediction: str = Field(min_length=1, max_length=10000)
    falsification_criterion: str = Field(min_length=1, max_length=10000)
    proposed_experiment: dict
    parent_id: str | None = None

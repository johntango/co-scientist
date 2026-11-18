"""Neutral, machine-readable model and metric documentation."""

from .models import SWEEP_PATHS, Experiment
from .policies import POLICIES, POLICY_VERSION


def describe(limits):
    return {
        "name": "TeamLab",
        "version": "1.0.0",
        "time_unit": "minutes",
        "description": "Identical-speed workers process exogenous A/B jobs non-preemptively. Eligibility, persistent task-type state, setup overhead, and scheduling determine service performance. No policy is presumed best.",
        "policies": [
            {"name": n, "version": POLICY_VERSION, "partitioned": p["partitioned"]} for n, p in POLICIES.items()
        ],
        "ordering": "At each time: integrate previous queue; finish jobs in configured worker order; admit all arrivals in workload order; dispatch idle workers in configured order. Equal arrivals use workload order. Jobs arriving exactly at observation end are not generated. Completions exactly at drain cap count; no new assignment at cap.",
        "intervals": "Measured arrival cohort: [warmup, warmup+observation). Observation-window completions: same half-open interval, including warm-up arrivals. Queue at observation end is after same-time completions/dispatch. Draining begins at observation end and lasts at most max_drain.",
        "metric_definitions": {
            "cohort.mean_wait": "Arrival to processing start, including setup after assignment; arithmetic mean over the complete measured arrival cohort.",
            "cohort.median_wait": "50th percentile of cohort wait; linear interpolation between order statistics.",
            "cohort.p95_wait": "95th percentile of cohort wait; linear interpolation between order statistics.",
            "cohort.mean_queue_delay": "Arrival to assignment, excluding subsequent setup.",
            "cohort.mean_setup": "Mean assigned setup delay in the cohort.",
            "cohort.mean_turnaround": "Arrival to completion, including queue, setup, and processing.",
            "cohort.completion_fraction": "Cohort jobs completed by simulation end divided by cohort arrivals; null for an empty cohort.",
            "window.throughput": "All completions in observation interval / observation minutes, including jobs arriving during warm-up.",
            "window.utilization": "Processing plus setup intersecting observation interval / (worker count × observation minutes).",
            "window.processing_minutes": "Sum of clipped processing time across all workers during observation.",
            "window.setup_minutes": "Sum of clipped setup time across all workers during observation.",
            "window.mean_queue_length": "Integral of unassigned waiting jobs over observation / observation minutes. Setup jobs have left this queue.",
            "window.end_queue_length": "Unassigned waiting jobs at observation end after dispatch; excludes workers in setup/processing.",
            "nominal_offered_load": "Expected processing work per minute / worker count, excluding setup. MMPP uses stationary state proportions, not an assertion of transient stationarity.",
            "realized_cohort_load": "Sum of intrinsic durations of cohort arrivals / (workers × observation minutes).",
        },
        "limits": vars(limits),
        "constraints": Experiment.model_json_schema(),
        "sweep_paths": sorted(SWEEP_PATHS),
        "limitations": [
            "Finite-horizon stylized simulation, not an empirically calibrated organization model; no learning, fatigue, failures, priorities, abandonment, or worker speed heterogeneity.",
            "Warm-up alone does not establish steady state; overloaded configurations can have growing queues.",
            "Incomplete cohort waiting/turnaround summaries and affected paired comparisons are null, not estimates from completed jobs alone.",
            "A recorded prediction is timestamped documentation, not proof that an agent had never seen related results.",
            "Independent replication-level paired percentile bootstrap; few replications give imprecise intervals. Sweeps are exploratory with no multiplicity adjustment.",
            "One local executor per data directory; local trusted use, no authentication or multi-user isolation. Cancellation is cooperative between simulation events/runs.",
            "Exponential service ignores cv; deterministic service has zero variability; lognormal cv is arithmetic standard deviation / mean.",
        ],
    }

# TeamLab demonstration: measured results

Experiment `02637e53d9ee4e22b7c42329ce1067bf`; status **completed**. Finished 2026-11-16T15:19:22.311091+00:00 (UTC).

This is an exploratory implementation demonstration, not a preregistered discovery or a claim about real teams. The conditions and seeds were fixed in `examples/teamlab/demo.json`; the model was not changed to force an outcome.

## Design

Four identical-speed workers; specialist/static partitions A,A,B,B; flexible eligibility A/B. Poisson rate 2.5 jobs/minute; exponential service with mean 1 minute for both types. Warm-up 100 minutes, observation 500, maximum drain 1000. Aging threshold 5 minutes. Setup 0 or 0.75 minutes; A share 0.5 or 0.8.

Independent workload seeds: `[101, 202, 303, 404, 505, 606, 707, 808, 909, 1010, 1111, 1212]`. Four policies × four conditions × 12 seeds = 192 policy runs. Each policy sees the identical immutable workload within each seed/condition. Setup-only changes reuse workload hashes. Confidence intervals use 2000 paired replication-level percentile bootstrap resamples, seed 2026.

## Mean cohort waiting time (minutes)

Entries below are equal-weight means of replication-level cohort means, not pooled job means. Waiting includes setup after assignment.

| Setup | A share | Specialist FCFS | Flexible FCFS | Type preference | Static partition |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.5 | 0.637 | 0.236 | 0.239 | 0.637 |
| 0 | 0.8 | 9.966 | 0.236 | 0.234 | 9.966 |
| 0.75 | 0.5 | 0.637 | 1.752 | 0.869 | 0.637 |
| 0.75 | 0.8 | 9.966 | 0.969 | 0.675 | 9.966 |

## Paired mean-wait differences versus specialists

Difference is policy minus specialist, in minutes. Negative means lower wait for that policy in these conditions. Intervals are pointwise 95% intervals; no multiple-comparison adjustment.

| Setup | A share | Comparison policy | Mean difference | 95% interval |
|---:|---:|---|---:|---|
| 0 | 0.5 | flexible_fcfs | -0.401 | [-0.460, -0.355] |
| 0 | 0.5 | type_preference | -0.399 | [-0.458, -0.350] |
| 0 | 0.5 | static_partition | 0.000 | [0.000, 0.000] |
| 0 | 0.8 | flexible_fcfs | -9.730 | [-14.396, -5.939] |
| 0 | 0.8 | type_preference | -9.732 | [-14.397, -5.946] |
| 0 | 0.8 | static_partition | 0.000 | [0.000, 0.000] |
| 0.75 | 0.5 | flexible_fcfs | 1.115 | [0.846, 1.429] |
| 0.75 | 0.5 | type_preference | 0.232 | [0.183, 0.297] |
| 0.75 | 0.5 | static_partition | 0.000 | [0.000, 0.000] |
| 0.75 | 0.8 | flexible_fcfs | -8.997 | [-13.525, -5.315] |
| 0.75 | 0.8 | type_preference | -9.292 | [-13.933, -5.537] |
| 0.75 | 0.8 | static_partition | 0.000 | [0.000, 0.000] |

## Interpretation and limitations

In these runs, flexible FCFS has lower mean waits than specialists at zero setup. At balanced demand and 0.75-minute setup, its difference is positive; type preference reduces the gap relative to flexible FCFS but is also above specialists. Under uneven demand, both flexible rules have lower observed mean waits than the fixed partition. This does not identify a universally best policy.

Static partition matches specialists exactly for every workload, including per-job assignments and timing. Its paired differences and intervals are all zero; this is an implementation control dictated by identical dispatch rules.

All 192 runs completed their measured cohorts (minimum completion fraction 1.000); total unfinished jobs 0.

Nominal aggregate offered load is 0.625 before setup. At A share 0.8, however, type A arrives at 2 jobs/minute against two specialist workers averaging 1 job/minute each: its nominal partition load is exactly 1. This has no stable M/M/2 steady-state waiting-time distribution. The large specialist waits are finite-horizon, capacity-constrained observations, not stationary estimates. Warm-up does not resolve that limitation. The flexible comparison changes access to idle capacity as well as scheduling; do not attribute the whole gap to scheduling alone.

Twelve replications provide only modest precision. The four-cell search is exploratory; intervals are unadjusted and seeds are shared across cells. Validate any broader claim with fresh seeds, longer horizons, alternative mix/rate combinations, burst processes, service distributions, and aging thresholds. The synthetic model has no empirical calibration, fatigue, learning, abandonment, or worker heterogeneity.

## Reproduce and inspect

```bash
mkdir -p teamlab-demo-data
teamlab --data-dir teamlab-demo-data sweep examples/teamlab/demo.json > teamlab-demo-data/demo-status.json
python scripts/teamlab_demo_report.py
teamlab --data-dir teamlab-demo-data export 02637e53d9ee4e22b7c42329ce1067bf --output ./exports
teamlab --data-dir teamlab-demo-data serve
```

Open <http://127.0.0.1:8000/?experiment=02637e53d9ee4e22b7c42329ce1067bf> when serving this data directory. A rerun creates a new experiment ID. The report script uses the ID in `demo-status.json`.

Validated configuration, all workload JSON files/hashes, raw per-job logs, queue traces, per-type metrics, paired differences and intervals, SVG, and provenance are in `teamlab-demo-data/experiments/02637e53d9ee4e22b7c42329ce1067bf/` plus its referenced workload files. The dashboard and API artifacts endpoint expose the downloads.

Source hash: `fe59191cdc4abd8a2019b5adbd6da7bf7c80547eebeb0930a130ec0d45b3c359`. Python 3.12.8. Dependencies: `{'fastapi': '0.163.1', 'pydantic': '2.12.5', 'simpy': '4.1.1', 'uvicorn': '0.54.0'}`.

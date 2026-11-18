# TeamLab

A local, hypothesis-neutral research sandbox for team organization and scheduling. SimPy supplies the simulation clock, Pydantic validates experiments, FastAPI exposes the shared engine, SQLite stores durable metadata, and a dependency-free browser UI renders results. No LLM, API key, external service, or CDN is used.

This package is independent of the existing Co-Scientist Gradio application. Start it on port **8000**; the original application can continue on **7860**.

## Install and launch

From this repository's root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
teamlab serve
```

Python 3.11 or newer on macOS or Linux is required (executor locking uses `fcntl`). TeamLab has no PyTorch dependency, so Intel or native ARM Python works. The editable install exposes `teamlab`; every command also works as `python -m teamlab`.

Open <http://127.0.0.1:8000> for the UI, <http://127.0.0.1:8000/docs> for interactive OpenAPI documentation, or <http://127.0.0.1:8000/openapi.json> for the full API contract.

## First experiment

While the server is running:

```bash
curl -sS http://127.0.0.1:8000/api/v1/experiments \
  -H 'Content-Type: application/json' \
  -d '{"mode":"comparison","seeds":[11,22,33,44,55],"setup_delay":0.5}'
```

The response immediately supplies an ID. Paste it into the dashboard's **Load** field or poll `/api/v1/experiments/ID`. Obtain results at `/api/v1/experiments/ID/results` and downloads at `/api/v1/experiments/ID/artifacts`.

Run an equivalent comparison without HTTP:

```bash
teamlab --data-dir teamlab-demo-data compare examples/teamlab/comparison.json
```

One executor owns a data directory at a time. CLI runs use a separate directory above to coexist with the server. `status`, `results`, `validate`, and `export` can read the server's data directory safely. To view CLI-produced experiments, stop the current server and run `teamlab --data-dir teamlab-demo-data serve`.

## Commands

```bash
teamlab describe
teamlab validate examples/teamlab/comparison.json
teamlab --data-dir teamlab-demo-data run examples/teamlab/single.json
teamlab --data-dir teamlab-demo-data compare examples/teamlab/comparison.json
teamlab --data-dir teamlab-demo-data sweep examples/teamlab/sweep.json
teamlab --data-dir teamlab-demo-data status EXPERIMENT_ID
teamlab --data-dir teamlab-demo-data results EXPERIMENT_ID --format json
teamlab --data-dir teamlab-demo-data export EXPERIMENT_ID --output ./exports
teamlab --data-dir teamlab-demo-data serve --port 8000
```

CLI stdout is JSON except for the HTTP server's logs. Exit codes: `0` success, `2` invalid input or operational error, `3` failed/cancelled/interrupted/incomplete experiment, `130` keyboard interruption. A run waits for completion; HTTP submission is asynchronous.

## Design an investigation

Use the form or the authoritative full-configuration JSON editor. Worker count is the length of `workers`; customize eligibility and partition assignments there. `service_b: null` copies A's distribution. MMPP has an explicit initial state and two states, each with `rate_multiplier`, `p_a`, and `transition_rate` (per minute). `aging_threshold` is a per-policy waiting-time threshold in minutes. Form presets describe conditions, not expected winners.

Use distinct seeds for independent replications. Each policy receives precisely the same immutable jobs for a seed and condition. Sweeps support up to two numeric axes, covering arrival rates, demand mix, state transition rates, volume multipliers, service means/CVs, setup delay, and aging threshold. The schema and environment endpoint list exact paths. To vary distributions, worker eligibility, or larger design structures, submit separate explicitly configured experiments.

Append-only research records store a question, competing hypothesis, mechanism, numerical prediction, falsification criterion, proposed configuration, optional parent ID, and timestamp. Link a record using `research_record_id` when submitting an experiment. Do not interpret a timestamp as evidence of no prior exposure to related results.

## Persistence and limits

Default data directory: `teamlab-data/`, overridable with `--data-dir` or `TEAMLAB_DATA_DIR`. SQLite metadata is in `teamlab.sqlite3`; workloads are SHA-256-addressed JSON; experiment files are under `experiments/ID/`. Data directories are gitignored.

Default execution limits, configurable through environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `TEAMLAB_MAX_JOBS` | 20000 | Maximum generated jobs per workload; exceeding it fails, never truncates |
| `TEAMLAB_MAX_REPLICATIONS` | 30 | Independent seeds per experiment |
| `TEAMLAB_MAX_SWEEP_CELLS` | 25 | Cartesian parameter combinations |
| `TEAMLAB_MAX_JOB_EVALUATIONS` | 500000 | Sum of jobs × policies across all cells and seeds; memory/work bound |
| `TEAMLAB_MAX_RUNS` | 400 | Cells × seeds × policies |
| `TEAMLAB_MAX_CONCURRENT` | 2 | Concurrent experiment threads |
| `TEAMLAB_MAX_PENDING` | 8 | Total admitted queued/running experiments |
| `TEAMLAB_MAX_SECONDS` | 300 | Cooperative wall-clock execution budget per experiment |

Additional schema hard ceilings: 100 seeds, 64 workers, four distinct built-in policies, two sweep axes with up to 20 unique values each, bounded numeric parameters, 1 MB HTTP request bodies, and an arrival/state-transition event budget. Local trusted use only; the server binds to `127.0.0.1`. No arbitrary code execution endpoint exists. Cancellation is checked between simulation events and stages; bootstrap and artifact-writing stages are bounded but not forcibly preempted. On restart, previously queued/running/cancelling experiments become `interrupted`.

Incomplete drains are first-class results. The API status becomes `incomplete`, counts and completion fractions remain visible, and affected cohort waiting/turnaround metrics and comparisons are null. They are not computed on a favorable completed subset. A failed/cancelled experiment does not publish partial results as completed evidence.

Artifacts include validated configuration, results JSON, metric CSV, paired-comparison CSV, a standalone SVG chart, metadata/provenance JSON, workload JSON, and optional detailed job CSV. Turn off `detailed_logs` to omit full per-job outputs (the first 60 jobs remain for inspection). Queue traces remain available.

## Reproduce and validate

`teamlab export` includes original workloads and configuration. Regenerating from the exported configuration with the same TeamLab source hash, Python, platform and dependency versions reproduces scientific outputs; IDs and wall-clock timestamps are operational metadata. Workload stream seeds and hashes are persisted. To replay a workload without regenerating it:

```python
import json
from teamlab.engine import simulate
from teamlab.models import Experiment

config = Experiment.model_validate_json(open("exports/config.json").read())
workload = json.load(open("exports/workload-HASH.json"))
# For sweep exports, select the matching expanded variant before simulation.
result = simulate(workload, config, config.policies[0])
```

When replaying in a fresh data directory, remove operational `research_record_id` linkage from the configuration; to regenerate instead of loading an existing workload, also remove `workload_id`. Linked records are exported separately as `research-record.json`.

For persisted API workloads, supply `workload_id`, its exact configuration, and its single original seed. Hash integrity is checked when loading the workload.

Standalone tests:

```bash
python -m pip install pytest==9.1.1 httpx==0.28.1 ruff==0.15.20
python -m pytest tests/test_teamlab.py
```

Full repository checks require the original Co-Scientist dependencies:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
make test
make lint
```

The full original ML stack needs a supported native Python/PyTorch platform; it is unnecessary for standalone TeamLab. Never run `make test-all` unless live tests are explicitly intended.

See [model specification](../docs/model.md), [API examples](../docs/api.md), [agent onboarding](../docs/co_scientist_quickstart.md), and [demonstration results](../docs/teamlab-demo.md).

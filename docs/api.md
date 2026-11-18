# TeamLab HTTP API v1

Start with `teamlab serve` (localhost:8000). Interactive docs: `/docs`; OpenAPI: `/openapi.json`. No credentials needed. Requests and responses are JSON except artifact downloads. Errors use `{"error":{"code":"...","message":"..."}}`; validation errors return 422, unknown IDs 404, full queues 429, and results not yet available 400. The server is intended for trusted local use, not public hosting.

```bash
curl -sS http://127.0.0.1:8000/api/v1/environment
curl -sS http://127.0.0.1:8000/api/v1/schema
curl -sS http://127.0.0.1:8000/api/v1/validate \
  -H 'Content-Type: application/json' -d @examples/teamlab/comparison.json
```

`environment` describes rules, policies and versions, timing, metric definitions, parameter constraints and execution limits without predicting a winner. `schema` returns the Pydantic experiment JSON schema. Optional `validate` expands/revalidates a sweep and checks bounds without executing it.

## Workloads

```bash
curl -sS http://127.0.0.1:8000/api/v1/workloads \
  -H 'Content-Type: application/json' \
  -d '{"seed":11,"config":{"warmup":100,"observation":500}}'
curl -sS http://127.0.0.1:8000/api/v1/workloads/WORKLOAD_ID
curl -sS http://127.0.0.1:8000/api/v1/workloads/WORKLOAD_ID/download -o workload.json
```

Generation is synchronous and bounded. The response includes ID, hash, original configuration, seed and separate stream seeds, job count, creation time and download link. Workloads are immutable, content-addressed local files. To reuse one, set `workload_id` in the experiment and copy its exact validated `config` into `workload` and its seed into a one-element `seeds` list. Existing workloads cannot be combined with a sweep.

## Experiments

```bash
curl -sS http://127.0.0.1:8000/api/v1/experiments \
  -H 'Content-Type: application/json' -d @examples/teamlab/comparison.json
curl -sS http://127.0.0.1:8000/api/v1/experiments/EXPERIMENT_ID
curl -sS http://127.0.0.1:8000/api/v1/experiments/EXPERIMENT_ID/results
curl -sS http://127.0.0.1:8000/api/v1/experiments/EXPERIMENT_ID/artifacts
curl -sS -X POST http://127.0.0.1:8000/api/v1/experiments/EXPERIMENT_ID/cancel
```

Submission returns 202 with an experiment ID immediately. Persistent states: `queued`, `running`, `cancelling`, then `completed`, `incomplete`, `failed`, `cancelled`, or `interrupted`. Status includes full validated config, progress (completed/total policy runs), timestamps, provenance, warnings and structured failure details. Cancellation is cooperative and idempotent for terminal states. A cancelled experiment does not expose partial results as evidence. Shutdown signals cancellation; an unclean restart marks unfinished work interrupted.

Results are available for `completed` and `incomplete`. Shape:

```json
{
  "experiment_id": "...",
  "exploratory": false,
  "warnings": [],
  "cells": [{
    "parameters": {},
    "runs": [{"policy":"...", "seed":11, "workload_hash":"...", "cohort":{}, "window":{}, "per_type":{}, "workers":[], "warnings":[]}],
    "comparisons": [{"baseline":"...", "policy":"...", "metric":"cohort.mean_wait", "direction":"policy minus baseline", "mean":0.0, "ci95":[0.0,0.0], "n":5, "replications":[], "bootstrap":{}}]
  }]
}
```

Each run also includes nominal and realized offered load, unfinished counts, a waiting histogram, exact queue trace and a first-60-arrival recorded timeline; `jobs` is present when detailed logs are enabled. Artifact listing gives `name` and `url` for configuration/results/metadata JSON, metrics/paired/job CSV, SVG and workload JSON downloads. Download each listed URL; no arbitrary filesystem path is accepted.

## Research records

```bash
curl -sS http://127.0.0.1:8000/api/v1/research-records \
 -H 'Content-Type: application/json' \
 -d '{"question":"Does the partition control reproduce specialists?","hypothesis":"With identical assignments, the two schedules agree exactly.","mechanism":"Their admissible jobs and dispatch rules coincide.","quantitative_prediction":"Every paired metric difference is zero.","falsification_criterion":"Any nonzero difference under identical workloads and assignments.","proposed_experiment":{"seeds":[11,22,33]},"parent_id":null}'
curl -sS http://127.0.0.1:8000/api/v1/research-records
```

Use the returned ID as `research_record_id` on future experiments. Records are append-only, include UTC timestamps, and list linked experiments. To revise, append a child with `parent_id`. Timestamps document statements; they do not establish that no earlier related results were seen. The `proposed_experiment` object is a research note, not automatically executed or required to be a fully validated configuration.

## Python client

```python
import json
from teamlab.client import Client

client = Client()
config = json.load(open("examples/teamlab/comparison.json"))
submitted = client.submit(config)
status = client.wait(submitted["id"])
if status["status"] in ("completed", "incomplete"):
    results = client.results(submitted["id"])
else:
    print(status["error"] or status["warnings"])
```

`Client.request(path, body=None)` supports all other endpoints; `Client.cancel(id)` cancels explicitly. `wait` timing out does not cancel automatically.

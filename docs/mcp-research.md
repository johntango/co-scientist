# Experimental research with MCP

Co-Scientist now discovers and calls operator-configured MCP tools. TeamLab is the first simulator adapter. The **Experimental Research (MCP)** tab runs a dedicated experiment coordinator with an OpenRouter tool-capable model. Existing single-agent and swarm generation remain available; choose their candidates in the experimental tab to test them. Session evidence links are attached to the selected in-memory hypotheses without changing Elo. Durable candidate snapshots and evidence live in the research journal.

## Start locally

Run from the repository/worktree root with the existing Co-Scientist `venv`:

```sh
venv/bin/pip install -r requirements-mcp.txt -r requirements-teamlab.txt
venv/bin/python -m teamlab --data-dir teamlab-data serve --port 8000
```

In a second terminal, load the environment and start the UI. The existing app expects `OPENROUTER_API_KEY`; the experimental coordinator also accepts `ROUTER_API_KEY` directly. Neither service prints keys.

```sh
set -a
source .env
set +a
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$ROUTER_API_KEY}"
venv/bin/python app.py
```

In an isolated worktree, source the original checkout's `.env` path instead (for this worktree, `../../.env`). Do not copy credentials into MCP configuration. Open [Co-Scientist](http://127.0.0.1:7860), choose **Experimental Research (MCP)**, and click **Discover configured tools**, then explicitly select the server’s tools in the checkbox list. Discovery reports reachability; selection enables those tools for the next research run but does not execute them. Enter a research question, a tool-capable OpenRouter model ID, and budgets. Click **Run complete research workflow**. The default **Generate hypotheses automatically** option requires no prior Run Cycle. Running research makes paid/provider-metered LLM calls according to your selected model; discovery and TeamLab simulation do not require a key.

TeamLab's HTTP executor must already be running. The MCP host launches `venv/bin/python -m teamlab.mcp_server` itself via stdio. It does not start another simulator executor. This works alongside the TeamLab dashboard.

Example question:

> Investigate when pooled generalists improve or worsen waiting relative to partitioned specialists. First discover TeamLab's assumptions and schema. Record a falsifiable quantitative prediction, compare policies using shared seeds, inspect paired uncertainty and unfinished cohorts, then revise the claim and seek a counterexample within the budget. Distinguish finite-horizon observations from steady-state claims. Report experiment IDs and artifacts.

## Command line and Python

Discovery does not call an LLM:

```sh
venv/bin/python -m app.mcp_tools mcp_servers.json
```

Research (uses your model/API key):

```sh
venv/bin/python scripts/experimental_research.py \
  "Investigate the conditions under which pooling reduces waiting." \
  --model openai/gpt-4.1-mini --max-experiments 3 --max-runs 120
```

Resume by adding `--resume SESSION_ID`. A completed session is returned without rerunning. A cancelled/interrupted session retains its experiment IDs; a new question should use a new session. The same model and server configuration are required. Only one coordinator can own a session at once. Ctrl+C and the UI cancellation button request cancellation of running simulator jobs. If a simulator is unreachable, its job may continue; the journal records cancellation errors for later recovery.

Python callers can use `await ExperimentAgent(servers, model).evaluate_hypotheses(question, candidates)`. Candidates can be `Hypothesis` objects or swarm dictionaries. All candidates get a **session-level** evidence link; the recorded predictions identify which claims were tested. This does not assert that every candidate was experimentally supported. Saved prior single/swarm run exports are not rewritten.

## Configuration and extension

`mcp_servers.json` is trusted operator configuration, not model-generated data. `CO_SCIENTIST_MCP_CONFIG` overrides its path for the UI. Each server specifies `transport`, connection details, and an explicit `allow_tools` list. Empty allowlists expose no tools. Names are qualified as `teamlab__submit_experiment` to avoid collisions. Models cannot add servers or launch arbitrary commands. Stdio subprocesses use the SDK's minimal inherited environment; API keys are not needed by TeamLab.

To use local Streamable HTTP instead:

```sh
venv/bin/python -m teamlab.mcp_server --transport streamable-http
```

Replace the TeamLab entry's `transport`, `command`, and `args` with:

```json
{"transport": "streamable-http", "url": "http://127.0.0.1:8001/mcp", "allow_tools": ["describe_environment", "get_experiment_schema", "validate_experiment", "record_prediction", "generate_workload", "submit_experiment", "get_experiment_status", "get_experiment_results", "cancel_experiment", "get_artifacts"]}
```

The adapter binds loopback. Remote authenticated server management/OAuth is not implemented. The SDK is pinned to `mcp==1.26.0`; do not substitute v2 API examples without migrating the client.

A new simulator can expose arbitrary allowlisted tools, with JSON-schema-validated arguments and structured results. To receive automatic experiment budgeting/polling/recovery, implement the same lifecycle contract: `validate_experiment(config)` returns `runs`; `submit_experiment(config, request_key)` returns `id` and `status`; status/cancel accept `experiment_id`. Statuses are `queued`, `running`, `cancelling`, followed by a terminal status. Submission must be durable and idempotent. Other arbitrary tools only receive the general call/time budget; their server owns additional resource limits.

## Evidence and execution guarantees

- Ten TeamLab tools cover description/schema, validation, prediction records, workload generation, submission, status, results, cancellation and artifact manifests.
- TeamLab requires a prediction record before MCP submission. The coordinator validates each new configuration and reserves experiment/run budgets before dispatch. Identical submissions within a session reuse a host-generated key; keys survive reconnects and simulator restarts. Reusing a key with changed configuration fails.
- Submission returns a durable job ID immediately. The coordinator polls without extra LLM calls, then passes terminal status to the model. Failed, interrupted and incomplete results are not rewritten as successes.
- Result retrieval returns one cell per call, including replication hashes/metrics and paired intervals. Full job logs and plots stay in downloadable artifacts. Tool responses are capped at 180 KB; callers should use smaller studies if a result cell exceeds the cap.
- Journals in `results/experiments/SESSION_ID.json` preserve source candidates, model messages, tool history, pre-dispatch keys, job statuses, predictions, returned evidence and final narrative. This directory is gitignored. Secrets are redacted before persistence/display.
- Independent of the model, budgets default to 32 tool calls, 3 submissions, 120 policy/seed/cell runs and 600 seconds; TeamLab enforces its own stricter resource limits. Each LLM response is capped at 3,000 output tokens. There is no dollar-cost estimator.
- Resume reconciles lost submission replies with the same key and completes partially answered tool-call batches. Prediction-record creation itself is append-only, not idempotent: a crash during record creation may leave an extra unlinked record. Simulator restart marks unfinished jobs interrupted; it does not silently rerun them.
- The model is checked for advertised tool support and requests a compatible provider. There is no silent text-only fallback. Tool-returned text is treated as data in the system instructions.

## Verification

`make test` runs offline, including a real stdio MCP connection to a temporary loopback TeamLab API and a scripted model that records, runs, reads evidence and revises. It also covers schema/allowlist enforcement, durable idempotency, resume, budgets, cancellation, model boundary and evidence linkage. `make lint` checks formatting and lint. No paid integration suite is needed for these tests.


### Reading the live activity panel

The dial animates only while an MCP request is in progress or a TeamLab job reports `running`. Model thinking has a separate text status and a stationary dial. Counters distinguish dispatched, successful and failed tool requests; coordinator validation and status polling are included. The panel lists experiment IDs, simulator-reported progress and whether results have actually been retrieved. A successful tool call alone is not a successful experiment. Failed or incomplete experiment statuses remain visible.

The original **Run Cycle — hypotheses only** button does not use simulator tools. Its output panel now explicitly labels numerical claims as model-generated proposals unless supported by linked experimental evidence. To enable tools, use the experimental tab; new sessions require discovery and explicit server selection. Rediscovery clears the selection. Selection changes affect the next run, not an already running session.


## Coordinated scientific workflow

The experimental tab and research CLI now run one ordered, resumable workflow:

1. **Discover tools and limits.** Connect to selected servers and read their environment descriptions and configuration schemas before generating hypotheses. Missing lifecycle tools stop the workflow with an error.
2. **Generate testable hypotheses.** Produce 1–3 claims and mechanisms grounded in those capabilities. Record unsupported aspects of the research goal explicitly. Imported single/swarm candidates can be refined instead.
3. **Design simulation questions.** Each hypothesis receives a question, quantitative prediction, metric, falsification criterion and executable configuration. All plans must pass simulator validation and fit the overall experiment/run budget before any submission. The planner receives combined validation feedback and can repair its plan twice. TeamLab sweeps have at most two axes; total runs count cells × seeds × policies across all questions. Specialist/generalist comparisons must permit cross-task eligibility for flexible workers, while specialist policy uses partitions. Terminal failures show underlying validation details rather than a transport TaskGroup wrapper.
4. **Run simulations.** The coordinator records predictions, submits jobs with durable keys, polls actual progress and retrieves each result cell and artifact manifest. Every question retains its hypothesis ID, prediction record ID and experiment ID.
5. **Evaluate and revise.** The model receives the actual returned results, including unsuccessful outcomes, to evaluate and revise each claim. Suggested follow-up experiments are clearly unexecuted.

The stage tracker shows pending, running, completed, failed or cancelled stages. Hypothesis cards and validated simulation questions stay visible while the activity panel shows actual MCP calls and simulator progress. Predictions are labelled separately from findings. Planning and review model calls have no execution tools; only the coordinator submits validated plans after the preceding stages finish. The model can correct a malformed hypothesis/experiment plan once; persistent errors stop the workflow before execution.

A run with no retrieved evidence ends as `no_evidence`, not a successful experimental finding. Failed/incomplete jobs remain identified even if the review stage finishes. Resume reuses saved plans, prediction IDs and submission keys; completed experiments with collected evidence are not rerun. Journals additionally store stage outputs and model requests/responses. Follow-up studies require a new coordinated run.

For advanced integrations, `ExperimentAgent(..., coordinated=False)` retains the original free-form tool loop. The UI and `run_from_config` use `coordinated=True`. Resume with the same workflow mode.

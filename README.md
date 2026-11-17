---
title: Co-Scientist Swarm
emoji: 🐝
colorFrom: gray
colorTo: blue
sdk: gradio
sdk_version: 6.19.0
python_version: 3.12
app_file: app.py
pinned: false
license: mit
short_description: Generate, debate-rank and export research hypotheses
---

# Co-Scientist Swarm

**Saleem Al Dajani** · [GitHub](https://github.com/saleemaldajani) · [Hugging Face](https://huggingface.co/sdajani)

**Demo:** https://huggingface.co/spaces/sdajani/co-scientist-public-demo

Language models can propose research hypotheses faster than anyone can check them. The bottleneck is no longer generation but ranking and verification: deciding which hypotheses are worth testing, and checking that each one names real data and a test that can actually be run. I built this fork to study that bottleneck. It generates hypotheses for a research goal, ranks them by structured debate between model judges, and exports every ranked idea with its review so it can be audited and tested outside the app.

## What this fork adds

### Swarm mode
A single co-scientist asks one model call for all hypotheses at once, then ranks them by their review labels. Swarm mode instead divides the goal across several agents and ranks the pooled hypotheses by debate:

1. **Orchestrator.** Splits the goal into distinct research directions, each with an objective, the data types to favor, and boundaries.
2. **Workers.** One per direction, generating hypotheses in parallel.
3. **Proximity.** Merges near-duplicates by sentence-embedding similarity, so repeats do not dilute the ranking.
4. **Reflection.** Reviews each hypothesis for novelty and feasibility, with references.
5. **Tournament.** An LLM judge argues for and against each of two hypotheses, then picks a winner; Elo ratings update after every debate, with Swiss pairing by current rating and randomized order.
6. **Meta-review.** Reads the ranking, reviews and the judges' reasons, and writes guidance for improvement.
7. **Evolution.** Rewrites the top hypotheses from their critiques; the rewrites re-enter the tournament in the next round.

Every model call, debate and merge is logged with the run. Single co-scientist mode remains available, so the two can be compared on the same goal and model.

### Attach data
Models only read text, so the stock app saw nothing but the goal and invented its numbers. Attach tables
(`.csv`, `.tsv`) and text files (`.md`, `.txt`, `.json`, such as a README or data dictionary) next to the goal, or load
the goal itself from a file:

- **An extract per step.** Each prompt gets about 20,000 characters of the data: text files and small tables whole,
  and for large tables the rows most relevant to that step (the goal for the orchestrator, a worker's direction, the two
  ideas a judge compares, the idea being rewritten).
- **A check of every quoted number.** After the run, each number a hypothesis quotes is looked up in the files and shown
  as found, with its file and row, or not found. The check is in the results panel and in **Export results**.

Attached data is sent to the model provider you select; with a local model it stays on your machine.

### Export results
Tick **Export results** (on by default) to download a zip of each run:

| File | Contents |
|---|---|
| `share/ideas_bundle.json` | every ranked idea: text, Elo, review labels, references, debate record and its worker's direction |
| `share/directions.json` | each assigned worker's ID, lens and full direction, in assignment order |
| `share/ideas_bundle.md`, `share/ideas_bundle.html` | worker directions, a leaderboard, then each idea with its direction, summary, full text, review and references |
| `share/idea_rankNN.html` | one page per idea, including its worker direction and navigation between ranks |
| `reviews/review_<id>.md` | the review of each idea, with its debates in swarm mode |
| `references.csv`, `references.md` | every reference, with the idea it belongs to |

Directions come from the stored worker assignments, including workers with no ranked ideas. Single-mode runs and
older runs without stored assignments export an empty `directions.json` list and `null` idea directions.

References are written by the model, not retrieved, so resolve each one before relying on it.

## Quick start

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export OPENROUTER_API_KEY=your_key      # https://openrouter.ai/keys; free models need no credit
python app.py                           # http://localhost:7860
```

Enter a research goal, choose **Single co-scientist** or **Swarm** under Mode, adjust the swarm settings if needed, and run.

### Timeouts and retries

Single and swarm runs each have a default 20-minute UI timeout. Set these environment variables before starting the app
to adjust the limits (values are in seconds):

| Variable | Default | Controls |
|---|---|---|
| `CO_SCIENTIST_CYCLE_TIMEOUT_SECONDS` | `1200` | single-mode cycle timeout |
| `CO_SCIENTIST_SWARM_TIMEOUT_SECONDS` | `1200` | swarm run timeout |
| `CO_SCIENTIST_LLM_CALL_MAX_SECONDS` | `150` | deadline for waiting on an individual model call |
| `CO_SCIENTIST_RATE_LIMIT_WAITS` | `6,15` | waits before retrying a rate-limited model |

The effective per-call deadline is the larger of `CO_SCIENTIST_LLM_CALL_MAX_SECONDS` and
`llm_request_timeout_seconds` in `config.yaml`. A call that exceeds it is treated as timed out; the underlying request
may continue in a background thread, but its result is discarded. Recoverable failures can trigger fallback models;
automatically selected fallback IDs containing `reason` are tried last. This name-based rule does not detect every
reasoning model.

### Local open models
The app talks to any OpenAI-compatible endpoint, so it can run entirely on a local machine, for example with [Ollama](https://ollama.com):

```yaml
# config.yaml
openrouter_base_url: "http://localhost:11434/v1"
llm_model: "gpt-oss:120b"               # or gemma4:31b, ...
llm_request_timeout_seconds: 3600       # large local models are slow
```

Set `OPENROUTER_API_KEY` to any non-empty string (Ollama ignores it). For long research prompts, give the model a large enough context window, for example with an Ollama Modelfile containing `PARAMETER num_ctx 32768`.

### From Python

```python
from app.swarm import SwarmCoScientist
from app.export import directions_from_lenses, ideas_from_hypotheses, write_bundle

from app.data_context import DataBundle

goal = "Propose testable hypotheses using the attached data."
data = DataBundle.from_dir("my_tables/")  # optional
res = SwarmCoScientist(goal, model="gpt-oss:120b", n_workers=4, per_worker=10, rounds=2, data=data).run()
final = [h for h in res["hypotheses"] if h["id"] in res["final"]]
write_bundle(
    ideas_from_hypotheses(final, res["matches"]),
    "results/my_run",
    "my_run",
    goal,
    mode="swarm",
    directions=directions_from_lenses(res.get("lenses")),
)
```

## Tests

```bash
make test     # offline: no key and no network; the model calls are mocked
make lint
```

## Credits and license

This is a fork of [llnl/open-ai-co-scientist](https://github.com/llnl/open-ai-co-scientist) (LLNL-CODE-2010270), whose single co-scientist cycle, interface and run history it builds on. Released under the MIT License (see `LICENSE` and `NOTICE`).

The swarm design follows the tournament, evolution and meta-review of Co-Scientist (Gottweis et al., *Nature*, 2026), the routines and handoffs of OpenAI Swarm, orchestrator-worker research agents, and multi-agent debate (Du et al., 2023).

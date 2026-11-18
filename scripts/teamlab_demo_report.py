"""Build a factual Markdown demonstration report from saved TeamLab artifacts."""

import argparse
import json
from pathlib import Path
from statistics import fmean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="teamlab-demo-data")
    parser.add_argument("--output", default="docs/teamlab-demo.md")
    args = parser.parse_args()
    data = Path(args.data_dir)
    state = json.loads((data / "demo-status.json").read_text())
    result = json.loads((data / "experiments" / state["id"] / "results.json").read_text())
    config = state["config"]
    policies = [p["name"] for p in config["policies"]]
    lines = [
        "# TeamLab demonstration: measured results",
        "",
        f"Experiment `{state['id']}`; status **{state['status']}**. Finished {state['finished_at']} (UTC).",
        "",
        "This is an exploratory implementation demonstration, not a preregistered discovery or a claim about real teams. The conditions and seeds were fixed in `examples/teamlab/demo.json`; the model was not changed to force an outcome.",
        "",
        "## Design",
        "",
        "Four identical-speed workers; specialist/static partitions A,A,B,B; flexible eligibility A/B. Poisson rate 2.5 jobs/minute; exponential service with mean 1 minute for both types. Warm-up 100 minutes, observation 500, maximum drain 1000. Aging threshold 5 minutes. Setup 0 or 0.75 minutes; A share 0.5 or 0.8.",
        "",
        f"Independent workload seeds: `{config['seeds']}`. Four policies × four conditions × {len(config['seeds'])} seeds = {state['progress']['completed']} policy runs. Each policy sees the identical immutable workload within each seed/condition. Setup-only changes reuse workload hashes. Confidence intervals use {config['bootstrap_samples']} paired replication-level percentile bootstrap resamples, seed {config['bootstrap_seed']}.",
        "",
        "## Mean cohort waiting time (minutes)",
        "",
        "Entries below are equal-weight means of replication-level cohort means, not pooled job means. Waiting includes setup after assignment.",
        "",
        "| Setup | A share | Specialist FCFS | Flexible FCFS | Type preference | Static partition |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in result["cells"]:
        values = [fmean(r["cohort"]["mean_wait"] for r in cell["runs"] if r["policy"] == p) for p in policies]
        params = cell["parameters"]
        lines.append(
            f"| {params['setup_delay']:g} | {params['workload.arrivals.p_a']:g} | "
            + " | ".join(f"{v:.3f}" for v in values)
            + " |"
        )
    lines += [
        "",
        "## Paired mean-wait differences versus specialists",
        "",
        "Difference is policy minus specialist, in minutes. Negative means lower wait for that policy in these conditions. Intervals are pointwise 95% intervals; no multiple-comparison adjustment.",
        "",
        "| Setup | A share | Comparison policy | Mean difference | 95% interval |",
        "|---:|---:|---|---:|---| ",
    ]
    for cell in result["cells"]:
        for pair in cell["comparisons"]:
            if pair["metric"] == "cohort.mean_wait" and pair["baseline"] == "specialist_fcfs":
                params = cell["parameters"]
                ci = pair["ci95"]
                lines.append(
                    f"| {params['setup_delay']:g} | {params['workload.arrivals.p_a']:g} | {pair['policy']} | {pair['mean']:.3f} | [{ci[0]:.3f}, {ci[1]:.3f}] |"
                )
    runs = [r for cell in result["cells"] for r in cell["runs"]]
    lines += [
        "",
        "## Interpretation and limitations",
        "",
        "In these runs, flexible FCFS has lower mean waits than specialists at zero setup. At balanced demand and 0.75-minute setup, its difference is positive; type preference reduces the gap relative to flexible FCFS but is also above specialists. Under uneven demand, both flexible rules have lower observed mean waits than the fixed partition. This does not identify a universally best policy.",
        "",
        "Static partition matches specialists exactly for every workload, including per-job assignments and timing. Its paired differences and intervals are all zero; this is an implementation control dictated by identical dispatch rules.",
        "",
        f"All {len(runs)} runs completed their measured cohorts (minimum completion fraction {min(r['cohort']['completion_fraction'] for r in runs):.3f}); total unfinished jobs {sum(r['unfinished_jobs'] for r in runs)}.",
        "",
        "Nominal aggregate offered load is 0.625 before setup. At A share 0.8, however, type A arrives at 2 jobs/minute against two specialist workers averaging 1 job/minute each: its nominal partition load is exactly 1. This has no stable M/M/2 steady-state waiting-time distribution. The large specialist waits are finite-horizon, capacity-constrained observations, not stationary estimates. Warm-up does not resolve that limitation. The flexible comparison changes access to idle capacity as well as scheduling; do not attribute the whole gap to scheduling alone.",
        "",
        "Twelve replications provide only modest precision. The four-cell search is exploratory; intervals are unadjusted and seeds are shared across cells. Validate any broader claim with fresh seeds, longer horizons, alternative mix/rate combinations, burst processes, service distributions, and aging thresholds. The synthetic model has no empirical calibration, fatigue, learning, abandonment, or worker heterogeneity.",
        "",
        "## Reproduce and inspect",
        "",
        "```bash",
        "mkdir -p teamlab-demo-data",
        "teamlab --data-dir teamlab-demo-data sweep examples/teamlab/demo.json > teamlab-demo-data/demo-status.json",
        "python scripts/teamlab_demo_report.py",
        f"teamlab --data-dir teamlab-demo-data export {state['id']} --output ./exports",
        "teamlab --data-dir teamlab-demo-data serve",
        "```",
        "",
        f"Open <http://127.0.0.1:8000/?experiment={state['id']}> when serving this data directory. A rerun creates a new experiment ID. The report script uses the ID in `demo-status.json`.",
        "",
        f"Validated configuration, all workload JSON files/hashes, raw per-job logs, queue traces, per-type metrics, paired differences and intervals, SVG, and provenance are in `{args.data_dir}/experiments/{state['id']}/` plus its referenced workload files. The dashboard and API artifacts endpoint expose the downloads.",
        "",
        f"Source hash: `{state['provenance']['source_hash']}`. Python {state['provenance']['python']}. Dependencies: `{state['provenance']['dependencies']}`.",
    ]
    Path(args.output).write_text("\n".join(lines) + "\n")
    print(json.dumps({"report": args.output, "experiment_id": state["id"]}))


if __name__ == "__main__":
    main()

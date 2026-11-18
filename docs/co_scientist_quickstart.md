# Agent onboarding: TeamLab

Use this neutral research assignment:

> Investigate how worker eligibility, demand patterns, setup overhead,
> and scheduling rules affect service performance. Formulate competing,
> falsifiable hypotheses. Record predictions before running the corresponding
> experiments. Use paired workloads and independent replications. Seek
> counterexamples, distinguish scheduling effects from capacity effects,
> and test any proposed generalization on fresh configurations and seeds.

No particular model or agent framework is required. TeamLab embeds no LLM and does not call OpenRouter. An agent may call HTTP, invoke the CLI, or import `teamlab.client.Client`.

1. Read `GET /api/v1/environment` (or `teamlab describe`) and `GET /api/v1/schema`. Pay attention to cohort versus observation-window definitions, eligibility, setup occupancy, censoring and MMPP transients.
2. Formulate multiple mechanisms with quantitative predictions and explicit falsification criteria. Append each to `/api/v1/research-records`; reference a parent when refining a claim. A record is documentation, not proof of absence of previous exposure.
3. Build a configuration from an example in `examples/teamlab/`. Validate with `teamlab validate FILE` or `POST /api/v1/validate`. Use `mode: comparison`, multiple distinct seeds, and all relevant controls. Use a single replication only for debugging, not precision inference.
4. Submit with `research_record_id`, poll progress, and inspect failure/incomplete status before interpreting output. A single workload is shared across every policy in each seed/condition. Do not discard unsuccessful or censored replications to obtain a favorable result.
5. Read paired differences and replication-level bootstrap intervals. Do not use per-job sample sizes as the count of independent replications. Averages across replications in the demo give equal weight to each replication.
6. Examine per-type outcomes, setup versus processing, nominal per-type capacity, queue growth, and completion fractions. Use recorded timelines to understand dispatch, not as a live execution feed.
7. Use exploratory sweeps to find candidate patterns. The returned intervals are pointwise, without adjustment for searching multiple cells. Test proposed generalizations on separate configurations and fresh seeds. Vary observation length and warm-up; warm-up alone is not a stationarity check.
8. Export full configuration, workload hashes/files, metrics, provenance and records. Preserve both confirming and contradicting evidence. Report limitations and conditions under which claims failed.

For an existing immutable workload, retrieve its metadata, then set `workload_id`, its original validated workload config and its one original seed. For independent replications, let the runner generate one workload per seed instead.

Useful commands:

```bash
teamlab validate examples/teamlab/comparison.json
teamlab --data-dir teamlab-demo-data compare examples/teamlab/comparison.json
teamlab --data-dir teamlab-demo-data sweep examples/teamlab/sweep.json
teamlab --data-dir teamlab-demo-data results ID --format json
teamlab --data-dir teamlab-demo-data export ID --output ./exports
```

See [API examples](api.md) and [model equations](model.md). The demonstration report is intentionally separate from this neutral assignment.

## Built-in Co-Scientist connector

The Gradio app now includes **Experimental Research (MCP)**. See [MCP setup and workflow](mcp-research.md) for automatic discovery, tool execution, evidence collection and revision.

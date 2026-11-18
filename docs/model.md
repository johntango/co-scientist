# TeamLab model specification (version 1)

## Exogenous workload and units

All times are **minutes**. Arrival and transition rates are per minute. Generate arrivals in `[0, warmup + observation)` before any scheduling decisions. A job has ID, arrival, type A/B, and an intrinsic duration; all compared policies reuse this exact record. SHA-256 hashes cover the version, validated workload configuration, seed, derived stream seeds, and jobs using canonical JSON.

The three random streams use seeds derived from SHA-256 of `teamlab-v1:SEED:arrivals`, `...:types`, and `...:durations`. Changing dispatch or setup never consumes these streams. MMPP transitions and arrival clocks share the arrival stream; type and service draws have independent streams. A changed workload parameter can change arrival counts, so common seeds across sweep cells do not imply identical workloads across different arrival configurations. Within each cell, policy comparisons are strictly paired.

For homogeneous arrivals, interarrival time is exponential with rate `lambda = arrivals.rate`; A probability is `p_a`. Rate zero yields no arrivals.

For MMPP, the process starts in `initial_state` (0 or 1). In state s, arrival intensity is `lambda_s = rate × states[s].rate_multiplier`; each arrival is A with probability `states[s].p_a`. The transition out of s has rate `q_s = states[s].transition_rate` and moves to `1-s`. Competing exponential events have total rate `lambda_s+q_s`; choose a transition with probability `q_s/(lambda_s+q_s)`. Transitions are strictly positive; a state may have zero arrival intensity. Initial-state transients are retained. The stationary mixture used only for nominal load is `pi_0=q_1/(q_0+q_1)`, `pi_1=q_0/(q_0+q_1)`.

Durations are deterministic `m`, exponential with mean `m`, or lognormal parameterized by arithmetic mean `m` and CV `c`. For the latter, `sigma² = log(1+c²)` and `mu = log(m) - sigma²/2`, so `D=exp(N(mu,sigma²))`. CV is ignored for deterministic/exponential; their actual CVs are 0/1. B defaults to A's distribution, but may have a separate one. Durations do not depend on worker or policy.

## Workers and dispatch

The default four workers have flexible eligibility `{A,B}` and partition assignments `A,A,B,B`. Each holds at most one job, including its setup. Task-type state initially is unset, then persists while idle. First assignment has no setup. A switch incurs `setup_delay`; processing is non-preemptive. Assignment, processing start, and completion are distinct times. Job `setup` is the assigned full setup duration; `setup_elapsed` is the actual setup incurred by simulation end (possibly shorter for capped runs). Worker IDs and input job IDs are unique.

A single SimPy process advances the central dispatcher to the next arrival, completion, interval boundary, or drain cap. At each timestamp it:

1. Integrates the queue from the prior timestamp.
2. Completes all jobs due, in configured worker order.
3. Admits all arrivals due, in workload input order for ties.
4. Dispatches idle workers in configured order, updating queue membership immediately.

This explicitly batches simultaneous events and prevents duplicate assignments. No assignment occurs at the drain cap; completions exactly at the cap do count. Arrival order, not alphabetical ID order, breaks equal-arrival ties. Setup-end instants need no dispatch event because the worker remains occupied; planned intervals record them exactly.

Policies:

- **Specialist FCFS**: worker eligibility intersected with its single partition type. Select oldest eligible job. A filtered central queue is equivalent to two eligibility-restricted FCFS queues.
- **Flexible global FCFS**: use configured eligibility without partition restriction. Select oldest eligible job.
- **Flexible type preference with aging**: among eligible jobs, if oldest wait is at least the threshold, select it; otherwise select the oldest job matching current type. If no match or no current type, select oldest eligible. Never deliberately idle.
- **Flexible static partition**: same partition assignments and dispatch rule as specialists, despite underlying flexible capability. It must reproduce the specialist schedule exactly.

For a custom configuration, eligibility still constrains every policy. A workload task with no eligible worker under a selected policy fails explicitly. New policies add a selector and partition behavior in `teamlab/policies.py` and extend `PolicyName` in the schema; engine event handling and metrics are unchanged. Selectors receive eligible jobs only and return an index; they must be deterministic and work-conserving.

## Cohort and window measurements

Let `L=warmup`, `H=warmup+observation`, and `C=H+max_drain`.

Measured arrival cohort: jobs arriving in `[L,H)`. Warm-up jobs stay in the model, consume resources, and affect cohort waits, but are excluded from cohort averages. All arrivals stop at H; the model drains through at most C.

For job j: queue delay `assignment-arrival`; waiting time `processing_start-arrival = queue_delay+setup`; turnaround `completion-arrival`. Mean, median, and p95 use the measured arrival cohort, including its completions after H. Quantiles interpolate linearly at index `(n-1)p`. If any measured cohort job is unfinished at C, cohort waiting/turnaround/queue-delay/setup summaries are **null**, even if some waiting times are already known. Completion counts/fractions remain available; empty-cohort fractions/means are null. This conservative rule avoids completed-only censoring bias. Full-run incompleteness also counts warm-up jobs, which can be unfinished even if the measured cohort is empty.

Observation-window metrics use `[L,H)` and include warm-up arrivals' activity:

- Throughput = number of completions in `[L,H)` / `(H-L)`.
- Worker processing/setup minutes = overlap of each respective interval with `[L,H)`.
- Utilization = sum of processing and setup overlap / `(workers × (H-L))`.
- Queue length counts **unassigned** jobs; a job in setup is busy, not queued.
- Time-weighted queue length = integral of queue length over `[L,H)` / `(H-L)`.
- End queue = queue after any completion and dispatch at H.

Per-type metrics use identical definitions. Setup time is attributed to the destination type. Completed jobs per minute differ from cohort completion fraction. Full-duration job records can exceed observation-window processing time because they include warm-up/drain.

Nominal offered load excludes setup: `(lambda_A × mean_A + lambda_B × mean_B) / worker_count`, using homogeneous or stationary MMPP type rates. Realized cohort load uses the sum of the actual generated cohort durations divided by observation time and worker count. Type workload is also compared with each type's eligible capacity. Aggregate load below one does not guarantee a partition is stable. Warnings flag overall or per-type nominal load at capacity, and every run warns that warm-up alone does not establish steady state.

## Inference and limitations

Within each independent seed, paired difference = second policy metric minus baseline policy metric. Bootstrap whole replication-level differences with replacement, not individual jobs. Report arithmetic mean difference and 2.5/97.5 percentiles from `bootstrap_samples` resamples using `bootstrap_seed`. At least two independent pairs are required for an interval; very few pairs still give weak inference. If any pair is undefined, the aggregate mean and interval are withheld rather than dropping the pair. Raw differences, hashes, sample count, method and seed are persisted. Exactly equal controls produce a zero-width interval; this is structural equality, not a universal empirical claim.

Sweeps are explicitly exploratory; intervals are pointwise, unadjusted for multiplicity. Fresh seeds and out-of-sample configurations are needed to evaluate generalizations. This synthetic model omits learning, fatigue, failures, worker heterogeneity, priorities, deadlines, abandonment and empirical calibration. No causal claim about real teams follows automatically.

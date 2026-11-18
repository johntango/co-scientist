"""Offline scientific and interface validation for the TeamLab engine."""

import copy
import json
import threading
import time
from statistics import fmean

import pytest
from fastapi.testclient import TestClient

from teamlab.api import create_app
from teamlab.engine import simulate
from teamlab.models import Experiment, WorkloadConfig, variants
from teamlab.runner import BusyError, Limits, Runner
from teamlab.statistics import bootstrap, compare
from teamlab.store import Store
from teamlab.workload import LimitExceeded, digest, generate


def configuration(**kwargs):
    raw = {
        "mode": "single",
        "workload": {"warmup": 0, "observation": 10},
        "policies": [{"name": "flexible_fcfs"}],
        "seeds": [1],
        "workers": [{"id": "w1", "partition": "A"}],
        "bootstrap_samples": 100,
    }
    raw.update(kwargs)
    return Experiment.model_validate(raw)


def fixed(config, entries):
    body = {
        "version": 1,
        "config": config.workload.model_dump(),
        "seed": config.seeds[0],
        "stream_seeds": {},
        "jobs": [{"id": f"j{i}", "arrival": a, "task": t, "duration": d} for i, (a, t, d) in enumerate(entries)],
    }
    return {"id": digest(body), "hash": digest(body), **body}


def run_fixed(entries, **kwargs):
    config = configuration(**kwargs)
    return simulate(fixed(config, entries), config, config.policies[0])


def test_hand_calculated_nonpreemptive_setup_and_queue():
    r = run_fixed([(0, "A", 2), (0, "B", 2), (1, "A", 2)], setup_delay=1)
    assert [j["assignment"] for j in r["jobs"]] == [0, 2, 5]
    assert [j["start"] for j in r["jobs"]] == [0, 3, 6]
    assert [j["completion"] for j in r["jobs"]] == [2, 5, 8]
    assert [j["setup"] for j in r["jobs"]] == [0, 1, 1]
    assert r["cohort"]["mean_wait"] == pytest.approx(8 / 3)
    assert r["cohort"]["median_wait"] == 3
    assert r["cohort"]["p95_wait"] == pytest.approx(4.8)
    assert r["cohort"]["mean_turnaround"] == pytest.approx(14 / 3)
    assert r["window"]["mean_queue_length"] == pytest.approx(0.6)
    assert r["window"]["processing_minutes"] == 6
    assert r["window"]["setup_minutes"] == 2
    assert r["window"]["utilization"] == 0.8


def test_empty_one_job_and_persistent_idle_state():
    empty = run_fixed([])
    assert empty["total_jobs"] == 0
    assert empty["cohort"]["mean_wait"] is None
    assert empty["window"]["utilization"] == 0
    one = run_fixed([(2, "A", 1)], setup_delay=99)
    assert one["jobs"][0]["setup"] == 0
    assert one["cohort"]["mean_wait"] == 0
    persistent = run_fixed([(0, "A", 1), (5, "B", 1)], setup_delay=2)
    assert persistent["jobs"][1]["start"] == 7


def test_simultaneous_arrival_completion_worker_and_job_tie_order():
    workers = [{"id": "second_lexically", "partition": "A"}, {"id": "first_lexically", "partition": "B"}]
    r = run_fixed([(0, "B", 2), (0, "A", 2), (2, "A", 1), (2, "B", 1)], workers=workers)
    assert [j["worker"] for j in r["jobs"]] == ["second_lexically", "first_lexically"] * 2
    assert [j["assignment"] for j in r["jobs"]] == [0, 0, 2, 2]


def test_preference_aging_and_no_deliberate_idling():
    entries = [(0, "A", 4), (1, "B", 1), (2, "A", 1)]
    preference = run_fixed(entries, policies=[{"name": "type_preference", "aging_threshold": 10}])
    aged = run_fixed(entries, policies=[{"name": "type_preference", "aging_threshold": 3}])
    assert [j["assignment"] for j in preference["jobs"]] == [0, 5, 4]
    assert [j["assignment"] for j in aged["jobs"]] == [0, 4, 5]
    no_match = run_fixed([(0, "A", 1), (2, "B", 1)], policies=[{"name": "type_preference"}])
    assert no_match["jobs"][1]["assignment"] == 2


def test_eligibility_nonoverlap_conservation_and_partition_equivalence():
    config = Experiment(workload=WorkloadConfig(warmup=5, observation=50), seeds=[52], setup_delay=0.7)
    workload = generate(config.workload, 52)
    before = copy.deepcopy(workload)
    runs = [simulate(workload, config, p) for p in config.policies]
    assert workload == before
    for r in runs:
        assert r["total_jobs"] == len(workload["jobs"]) == len(r["jobs"])
        assert len({j["id"] for j in r["jobs"]}) == len(r["jobs"])
        assert r["unfinished_jobs"] == 0
        for w in config.workers:
            jobs = sorted((j for j in r["jobs"] if j["worker"] == w.id), key=lambda j: j["assignment"])
            assert all(a["completion"] <= b["assignment"] for a, b in zip(jobs, jobs[1:]))
            assert all(j["task"] in w.eligibility for j in jobs)
            if r["policy"] in ("specialist_fcfs", "static_partition"):
                assert all(j["task"] == w.partition for j in jobs)
    a, b = copy.deepcopy(runs[0]), copy.deepcopy(runs[3])
    a.pop("policy")
    b.pop("policy")
    assert a == b
    assert runs[1] == simulate(workload, config, config.policies[1])


def test_warmup_observation_drain_clipping_and_boundary_completion():
    r = run_fixed([(0, "A", 4), (3, "B", 3)], workload={"warmup": 2, "observation": 3})
    assert r["cohort"]["arrivals"] == 1
    assert r["cohort"]["mean_wait"] == 1
    assert r["cohort"]["mean_turnaround"] == 4
    assert r["window"]["completed"] == 1
    assert r["window"]["throughput"] == 1 / 3
    assert r["window"]["processing_minutes"] == 3
    assert r["window"]["mean_queue_length"] == 1 / 3
    boundary = run_fixed([(0, "A", 5)], workload={"warmup": 2, "observation": 3}, max_drain=0)
    assert boundary["window"]["completed"] == 0
    assert boundary["jobs"][0]["completion"] == 5
    assert boundary["status"] == "complete"
    setup = run_fixed([(0, "A", 1), (1, "B", 1)], setup_delay=4, workload={"warmup": 2, "observation": 2})
    assert setup["window"]["setup_minutes"] == 2
    assert setup["window"]["processing_minutes"] == 0


def test_incomplete_drain_and_overload_never_completed_only_estimates():
    r = run_fixed([(0, "A", 1), (1, "B", 100), (2, "A", 1)], max_drain=0, setup_delay=20)
    assert r["status"] == "incomplete"
    assert r["unfinished_jobs"] == 2
    assert r["cohort"]["completion_fraction"] == 1 / 3
    assert r["cohort"]["mean_wait"] is None
    assert r["cohort"]["mean_turnaround"] is None
    assert r["jobs"][1]["start"] is None
    assert r["window"]["end_queue_length"] == 1
    assert any("capacity" in w for w in r["warnings"])


def test_separate_streams_reproducibility_and_mmpp_proportions():
    a = WorkloadConfig(warmup=0, observation=100)
    wa = generate(a, 15)
    assert wa == generate(a, 15)
    b = a.model_copy(deep=True)
    b.arrivals.p_a = 0.9
    wb = generate(b, 15)
    assert [(j["arrival"], j["duration"]) for j in wa["jobs"]] == [(j["arrival"], j["duration"]) for j in wb["jobs"]]
    c = a.model_copy(deep=True)
    c.service_a.distribution = "deterministic"
    wc = generate(c, 15)
    assert [(j["arrival"], j["task"]) for j in wa["jobs"]] == [(j["arrival"], j["task"]) for j in wc["jobs"]]
    burst = WorkloadConfig.model_validate(
        {
            "warmup": 0,
            "observation": 100,
            "arrivals": {
                "process": "mmpp",
                "rate": 2,
                "initial_state": 1,
                "states": [
                    {"p_a": 0, "rate_multiplier": 0, "transition_rate": 1},
                    {"p_a": 1, "rate_multiplier": 2, "transition_rate": 1},
                ],
            },
        }
    )
    w = generate(burst, 77)
    assert w == generate(burst, 77)
    assert {j["task"] for j in w["jobs"]} == {"A"}
    assert len(w["jobs"]) > 50


def test_lognormal_arithmetic_mean_and_cv():
    config = WorkloadConfig.model_validate(
        {
            "warmup": 0,
            "observation": 10000,
            "arrivals": {"rate": 3},
            "service_a": {"distribution": "lognormal", "mean": 2, "cv": 0.7},
        }
    )
    jobs = generate(config, 234, 40000)["jobs"]
    values = [j["duration"] for j in jobs]
    mean = fmean(values)
    cv = (fmean((v - mean) ** 2 for v in values) ** 0.5) / mean
    assert mean == pytest.approx(2, abs=0.05)
    assert cv == pytest.approx(0.7, abs=0.04)


def test_paired_replication_statistics_and_missing_pairs():
    runs = []
    for seed, a, b in [(1, 100, 102), (2, 1, 5), (3, 5, 11)]:
        for p, v in [("a", a), ("b", b)]:
            runs.append(
                {
                    "seed": seed,
                    "policy": p,
                    "workload_hash": str(seed),
                    "cohort": {"mean_wait": v, "p95_wait": v, "mean_turnaround": v},
                    "window": {"throughput": v, "utilization": v, "mean_queue_length": v},
                }
            )
    comp = compare(runs, ["a", "b"], 5, 2000)[0]
    assert comp["mean"] == 4
    assert [p["difference"] for p in comp["replications"]] == [2, 4, 6]
    assert comp["ci95"][0] <= 4 <= comp["ci95"][1]
    assert comp == compare(runs, ["a", "b"], 5, 2000)[0]
    assert bootstrap([0, 0, 0], 5, 100)["ci95"] == [0, 0]
    assert bootstrap([2], 5, 100)["ci95"] is None
    runs[0]["cohort"]["mean_wait"] = None
    comp = compare(runs, ["a", "b"], 5, 100)[0]
    assert comp["mean"] is None and comp["ci95"] is None and comp["missing_pairs"] == 1


def test_mm1_analytical_sanity_fixed_seeds():
    # M/M/1: lambda=.7, mu=1 -> Wq=2.3333, W=3.3333, utilization=.7.
    # Long fixed runs and a tolerance wider than Monte Carlo noise avoid flaky tests.
    means, utilizations, throughputs = [], [], []
    for seed in (618, 742):
        c = configuration(
            workload={"warmup": 2000, "observation": 40000, "arrivals": {"rate": 0.7, "p_a": 1}},
            seeds=[seed],
            detailed_logs=False,
        )
        r = simulate(generate(c.workload, seed, 50000), c, c.policies[0])
        means.append(r["cohort"]["mean_wait"])
        utilizations.append(r["window"]["utilization"])
        throughputs.append(r["window"]["throughput"])
    assert fmean(means) == pytest.approx(0.7 / 0.3, abs=0.3)
    assert fmean(utilizations) == pytest.approx(0.7, abs=0.025)
    assert fmean(throughputs) == pytest.approx(0.7, abs=0.02)


def wait_status(client, ident):
    for _ in range(200):
        response = client.get(f"/api/v1/experiments/{ident}").json()
        if response["status"] not in ("queued", "running", "cancelling"):
            return response
        time.sleep(0.01)
    pytest.fail("Experiment did not finish")


def test_api_validation_workload_submission_records_and_artifacts(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/api/v1/environment").json()["time_unit"] == "minutes"
        assert "properties" in client.get("/api/v1/schema").json()
        invalid = configuration().model_dump()
        invalid["setup_delay"] = -1
        assert client.post("/api/v1/experiments", json=invalid).status_code == 422
        assert client.get("/api/v1/experiments/not-found").status_code == 404
        wc = {"warmup": 0, "observation": 5}
        w = client.post("/api/v1/workloads", json={"config": wc, "seed": 1}).json()
        assert client.get(w["download"]).json()["hash"] == w["hash"]
        c = configuration(workload=w["config"], workload_id=w["id"])
        record = client.post(
            "/api/v1/research-records",
            json={
                "question": "q",
                "hypothesis": "h",
                "mechanism": "m",
                "quantitative_prediction": "p",
                "falsification_criterion": "f",
                "proposed_experiment": c.model_dump(),
            },
        ).json()
        c.research_record_id = record["id"]
        response = client.post("/api/v1/experiments", json=c.model_dump())
        assert response.status_code == 202
        ident = response.json()["id"]
        state = wait_status(client, ident)
        assert state["status"] == "completed", state
        r = client.get(f"/api/v1/experiments/{ident}/results").json()
        assert r["cells"][0]["runs"][0]["workload_hash"] == w["hash"]
        files = client.get(f"/api/v1/experiments/{ident}/artifacts").json()
        assert {"results.json", "jobs.csv", "waiting.svg", "metadata.json"}.issubset({f["name"] for f in files})
        assert all(client.get(f["url"]).status_code == 200 for f in files)
        assert client.get("/api/v1/research-records").json()[0]["experiments"][0]["id"] == ident
        assert client.patch("/api/v1/research-records/" + record["id"], json={}).status_code in (404, 405)
        # Read-only store access must not interrupt active ownership.
        assert Store(tmp_path).experiment(ident)["status"] == "completed"


def test_cancel_running_and_queued_experiments(tmp_path, monkeypatch):
    import teamlab.runner as module
    from teamlab.workload import Cancelled

    started = threading.Event()

    def slow(workload, config, policy, cancelled):
        started.set()
        while not cancelled():
            time.sleep(0.002)
        raise Cancelled()

    monkeypatch.setattr(module, "simulate", slow)
    limits = Limits()
    limits.max_concurrent = 1
    with TestClient(create_app(tmp_path, limits)) as client:
        c = configuration().model_dump()
        a = client.post("/api/v1/experiments", json=c).json()["id"]
        assert started.wait(2)
        b = client.post("/api/v1/experiments", json=c).json()["id"]
        assert client.post(f"/api/v1/experiments/{b}/cancel").status_code == 200
        assert client.post(f"/api/v1/experiments/{a}/cancel").status_code == 200
        assert wait_status(client, a)["status"] == "cancelled"
        assert wait_status(client, b)["status"] == "cancelled"
        assert client.get(f"/api/v1/experiments/{a}/results").status_code == 400


def test_restart_limits_sweep_validation_and_failure(tmp_path):
    store = Store(tmp_path)
    entry = store.create_experiment(configuration().model_dump(), {})
    store.update(entry["id"], status="running")
    runner = Runner(store)
    try:
        assert store.experiment(entry["id"])["status"] == "interrupted"
        with pytest.raises(BusyError):
            Runner(Store(tmp_path))
        runner.limits.max_jobs = 1
        e = runner.submit(configuration(workload={"warmup": 0, "observation": 10, "arrivals": {"rate": 100}}))
        for _ in range(200):
            state = store.experiment(e["id"])
            if state["status"] == "failed":
                break
            time.sleep(0.01)
        assert state["error"]["code"] == "LimitExceeded"
        sweep = Experiment(
            mode="sweep", seeds=[1, 2], sweep={"setup_delay": [0, 1], "workload.arrivals.p_a": [0.5, 0.8]}
        )
        assert len(list(variants(sweep))) == 4
        reverse = sweep.model_copy(update={"sweep": dict(reversed(list(sweep.sweep.items())))})
        assert [p for p, _ in variants(reverse)] == [p for p, _ in variants(sweep)]
        runner.limits.max_sweep_cells = 2
        with pytest.raises(LimitExceeded):
            runner.validate(sweep)
        invalid = sweep.model_copy(deep=True)
        invalid.sweep = {"workload.arrivals.p_a": [2]}
        with pytest.raises(ValueError):
            list(variants(invalid))
    finally:
        runner.close()


def test_exported_workload_reproduces_metrics_and_integrity_check(tmp_path):
    c = configuration(detailed_logs=False)
    w = generate(c.workload, 1)
    store = Store(tmp_path)
    store.save_workload(w)
    store.save_workload(w)
    restored = store.workload(w["id"], full=True)
    assert simulate(restored, c, c.policies[0]) == simulate(w, c, c.policies[0])
    path = store.root / "workloads" / f"{w['id']}.json"
    corrupted = json.loads(path.read_text())
    corrupted["seed"] = 2
    path.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="integrity"):
        store.workload(w["id"], full=True)


def test_concurrent_immutable_workload_publication(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path)
    workload = generate(WorkloadConfig(observation=20), 11)
    with ThreadPoolExecutor(max_workers=4) as pool:
        metadata = list(pool.map(lambda _: store.save_workload(workload), range(8)))
    assert all(m["id"] == workload["id"] for m in metadata)
    assert store.workload(workload["id"], full=True) == workload
    assert len(list((tmp_path / "workloads").glob("*.json"))) == 1


def test_job_evaluation_budget_and_incurred_setup(tmp_path):
    r = run_fixed([(0, "A", 1), (1, "B", 100)], max_drain=0, setup_delay=20)
    assert r["jobs"][1]["setup_elapsed"] == 9
    assert r["jobs"][1]["setup"] == 20
    runner = Runner(Store(tmp_path))
    runner.limits.max_job_evaluations = 1
    try:
        item = runner.submit(configuration())
        for _ in range(200):
            state = runner.store.experiment(item["id"])
            if state["status"] == "failed":
                break
            time.sleep(0.01)
        assert state["error"]["code"] == "LimitExceeded"
        assert "evaluation budget" in state["error"]["message"]
    finally:
        runner.close()


def test_cli_validate_run_results_export_and_exit_codes(tmp_path):
    import subprocess
    import sys

    config_file = tmp_path / "experiment.json"
    config_file.write_text(configuration(workload={"warmup": 0, "observation": 4}).model_dump_json())
    command = [sys.executable, "-m", "teamlab", "--data-dir", str(tmp_path / "data")]

    def cli(*args):
        return subprocess.run([*command, *args], capture_output=True, text=True, timeout=20)

    validation = cli("validate", str(config_file))
    assert validation.returncode == 0 and json.loads(validation.stdout)["valid"]
    run = cli("run", str(config_file))
    assert run.returncode == 0, run.stderr
    ident = json.loads(run.stdout)["id"]
    assert json.loads(cli("status", ident).stdout)["status"] == "completed"
    assert json.loads(cli("results", ident, "--format", "json").stdout)["cells"]
    exported = cli("export", ident, "--output", str(tmp_path / "export"))
    assert exported.returncode == 0, exported.stderr
    assert list((tmp_path / "export").glob("workload-*.json"))
    assert (tmp_path / "export" / "config.json").is_file()
    config_file.write_text('{"setup_delay": -1}')
    invalid = cli("validate", str(config_file))
    assert invalid.returncode == 2 and "error" in json.loads(invalid.stderr)


def test_api_incomplete_status_single_replication_and_no_full_logs(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        c = configuration(
            workload={
                "warmup": 0,
                "observation": 2,
                "arrivals": {"rate": 20},
                "service_a": {"distribution": "deterministic", "mean": 10},
            },
            max_drain=0,
            detailed_logs=False,
        )
        ident = client.post("/api/v1/experiments", json=c.model_dump()).json()["id"]
        state = wait_status(client, ident)
        assert state["status"] == "incomplete"
        result = client.get(f"/api/v1/experiments/{ident}/results").json()
        run = result["cells"][0]["runs"][0]
        assert run["cohort"]["mean_wait"] is None
        assert run["cohort"]["unfinished"] > 0
        assert "jobs" not in run
        assert "jobs.csv" not in {f["name"] for f in client.get(f"/api/v1/experiments/{ident}/artifacts").json()}

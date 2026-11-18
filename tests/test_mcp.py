"""Offline transport and research workflow tests: real MCP and real simulation, scripted LLM."""

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.experiment_agent import ExperimentAgent
from app.mcp_tools import ToolRegistry
from teamlab.api import create_app
from teamlab.models import Experiment

ROOT = Path(__file__).resolve().parents[1]


def configuration(url):
    config = json.loads((ROOT / "mcp_servers.json").read_text())["servers"]
    config["teamlab"]["command"] = sys.executable
    config["teamlab"]["args"] += ["--base-url", url]
    return config


@pytest.fixture
def api(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "data"), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)
    sock.close()
    assert not thread.is_alive()


def prediction(config):
    return dict(
        question="Does sharing reduce waiting?",
        hypothesis="Pooling reduces mean wait at zero setup.",
        mechanism="Idle capacity can serve either class.",
        quantitative_prediction="Mean difference < 0.",
        falsification_criterion="Paired CI wholly above zero.",
        proposed_experiment=config,
    )


def tiny():
    config = Experiment().model_dump()
    config["workload"].update(warmup=5, observation=25)
    config["seeds"] = list(range(10))
    config["bootstrap_samples"] = 100
    return config


def test_api_idempotency_survives_restart(tmp_path):
    config = tiny()
    with TestClient(create_app(tmp_path)) as client:
        first = client.post("/api/v1/experiments", json=config, headers={"Idempotency-Key": "retry"}).json()
        duplicate = client.post("/api/v1/experiments", json=config, headers={"Idempotency-Key": "retry"}).json()
        assert first["id"] == duplicate["id"]
        changed = {**config, "setup_delay": 99}
        assert client.post("/api/v1/experiments", json=changed, headers={"Idempotency-Key": "retry"}).status_code == 400
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.post("/api/v1/experiments", json=config, headers={"Idempotency-Key": "retry"}).json()["id"]
            == first["id"]
        )


def test_real_mcp_research_loop(api, tmp_path):
    agent = ExperimentAgent(configuration(api), "scripted", directory=tmp_path / "research")
    config = tiny()
    record = None
    experiment = None
    step = 0

    async def complete(messages, tools):
        nonlocal step, record, experiment
        assert len(tools) == 10
        last = json.loads(messages[-1]["content"]) if messages[-1]["role"] == "tool" else None
        if step == 3:
            record = last["id"]
            config["research_record_id"] = record
        if step == 5:
            experiment = last["id"]
            assert last["status"] == "completed"
        if step == 6:
            assert len(last["runs"]) == 40
            assert last["comparisons"]
            assert all(r["workload_hash"] for r in last["runs"])
        sequence = [
            ("describe_environment", {}),
            ("get_experiment_schema", {}),
            ("record_prediction", {"record": prediction(config)}),
            ("validate_experiment", {"config": config}),
            ("submit_experiment", {"config": config, "request_key": "model-key-is-overridden"}),
            ("get_experiment_results", {"experiment_id": experiment}),
            ("get_artifacts", {"experiment_id": experiment}),
            (
                "record_prediction",
                {
                    "record": {
                        **prediction(config),
                        "parent_id": record,
                        "hypothesis": "Refine the pooling claim using the observed paired intervals.",
                    }
                },
            ),
        ]
        if step == len(sequence):
            return {"role": "assistant", "content": f"Evidence collected from experiment {experiment}."}
        name, arguments = sequence[step]
        step += 1
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": str(step),
                    "type": "function",
                    "function": {"name": "teamlab__" + name, "arguments": json.dumps(arguments)},
                }
            ],
        }

    result = asyncio.run(agent.run("Test pooling with paired replications", complete))
    assert result["status"] == "completed", result.get("error", result["events"])
    assert result["tool_activity"]["completed"] >= 8
    assert result["tool_activity"]["current"] is None
    assert "teamlab__submit_experiment" in result["tool_activity"]["tools"]
    assert result["phase"] == "finished"
    assert len(result["evidence"]) == 4
    assert len(result["jobs"]) == 1
    assert result["runs"] == 40
    assert Path(agent.path).exists()
    assert record in Path(agent.path).read_text()
    # Completed sessions are immutable on resume; no second model call or experiment.
    resumed = ExperimentAgent(configuration(api), "scripted", directory=tmp_path / "research", session_id=result["id"])
    assert asyncio.run(resumed.run("ignored", complete))["jobs"] == result["jobs"]


def test_discovery_validation_and_allowlist(api):
    async def exercise():
        config = configuration(api)
        config["teamlab"]["allow_tools"] = ["describe_environment", "get_experiment_status"]
        async with ToolRegistry(config) as registry:
            assert len(registry.definitions()) == 2
            assert await registry.call("teamlab__describe_environment", {})
            with pytest.raises(ValueError, match="allowlist"):
                await registry.call("teamlab__submit_experiment", {})
            with pytest.raises(Exception):
                await registry.call("teamlab__get_experiment_status", {"experiment_id": 42})

    asyncio.run(exercise())


def test_agent_budgets_deduplication_cancel_and_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_API_KEY", "secret-test-key")

    class Registry:
        tools = {"teamlab__submit_experiment": True}
        submissions = 0
        cancelled = []

        async def call(self, name, args):
            if name.endswith("validate_experiment"):
                return {"runs": 4}
            if name.endswith("submit_experiment"):
                self.submissions += 1
                return {"id": "a" * 32, "status": "running"}
            if name.endswith("cancel_experiment"):
                self.cancelled.append(args["experiment_id"])
                return {"status": "cancelling"}
            return {"status": "completed"}

    async def exercise():
        registry = Registry()
        agent = ExperimentAgent({}, "fake", directory=tmp_path, max_experiments=1, max_runs=4)
        agent.deadline = time.monotonic() + 30
        args = {"config": {"research_record_id": "r"}, "request_key": "x"}
        await agent.execute(registry, "teamlab__submit_experiment", args)
        await agent.execute(registry, "teamlab__submit_experiment", args)
        assert agent.state["runs"] == 4
        assert len(agent.state["submissions"]) == 1
        with pytest.raises(ValueError, match="budget"):
            await agent.execute(registry, "teamlab__submit_experiment", {**args, "config": {"research_record_id": "s"}})
        agent.state["jobs"]["a" * 32]["status"] = "running"
        await agent.cancel_jobs(registry)
        assert registry.cancelled == ["a" * 32]
        agent.event(error="secret-test-key")
        assert "secret-test-key" not in agent.path.read_text()
        agent.stop()
        with pytest.raises(asyncio.CancelledError):
            await agent.wait_job(registry, "teamlab__", "a" * 32)

    asyncio.run(exercise())


def test_resume_partial_batch_and_hypothesis_evidence(tmp_path, monkeypatch):
    from app import experiment_agent
    from app.models import Hypothesis

    class Registry:
        tools = {"sim__inspect": True}
        called = []

        def __init__(self, servers):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def definitions(self):
            return []

        async def call(self, name, args):
            self.called.append(args["number"])
            return {"value": args["number"]}

    monkeypatch.setattr(experiment_agent, "ToolRegistry", Registry)
    agent = ExperimentAgent({}, "fake", directory=tmp_path)
    agent.state["messages"] = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": str(n),
                    "type": "function",
                    "function": {"name": "sim__inspect", "arguments": json.dumps({"number": n})},
                }
                for n in (1, 2)
            ],
        },
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
    ]
    agent.state["status"] = "running"
    agent.save()

    async def complete(messages, tools):
        assert messages[-1]["tool_call_id"] == "2"
        return {"role": "assistant", "content": "No simulation evidence yet."}

    single = Hypothesis("h1", "Pooling", "Pooling lowers waiting")
    swarm = {"id": "h2", "text": "A counterexample", "elo": 1234}
    result = asyncio.run(agent.evaluate_hypotheses("question", [single, swarm], complete))
    assert Registry.called == [2]
    assert single.elo_score == 1200
    assert swarm["elo"] == 1234
    assert single.to_dict()["experimental_evidence"][0]["session_id"] == result["id"]
    assert swarm["experimental_evidence"][0]["session_id"] == result["id"]


def test_model_boundary_requires_tools_and_keeps_messages(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import experiment_agent

    class HTTP:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def get(self, url):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": [{"id": "fake", "supported_parameters": ["tools"]}]},
            )

    class Registry:
        def __init__(self, servers):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        def definitions(self):
            return [{"type": "function", "function": {"name": "sim__inspect", "parameters": {"type": "object"}}}]

    received = []

    def create(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        model_dump=lambda **kw: {"role": "assistant", "content": "No experiment was run."}
                    )
                )
            ]
        )

    monkeypatch.setenv("ROUTER_API_KEY", "fake-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(experiment_agent.httpx, "AsyncClient", HTTP)
    monkeypatch.setattr(experiment_agent, "ToolRegistry", Registry)
    monkeypatch.setattr(
        experiment_agent.utils,
        "OpenAI",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None
        ),
    )
    agent = ExperimentAgent({}, "fake", directory=tmp_path)
    result = asyncio.run(agent.run("Question"))
    assert result["status"] == "completed"
    assert received[0]["tools"] and received[0]["messages"][0]["role"] == "system"
    assert received[0]["extra_body"]["provider"]["require_parameters"]
    unsupported = ExperimentAgent({}, "text-only", directory=tmp_path)
    with pytest.raises(ValueError, match="tool support"):
        asyncio.run(unsupported.run("Question"))
    assert len(received) == 1


def test_streamable_http_transport(api):
    from teamlab.mcp_server import create_server

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_server(api).streamable_http_app(), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.02)

        async def exercise():
            config = {
                "teamlab": {
                    "transport": "streamable-http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                    "allow_tools": ["describe_environment"],
                }
            }
            async with ToolRegistry(config) as registry:
                assert len(registry.definitions()) == 1
                assert await registry.call("teamlab__describe_environment", {})

        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
    assert not thread.is_alive()


def test_tool_selection_and_truthful_activity():
    from app.experiment_ui import activity_html, select_servers, selection_summary

    config = {
        "teamlab": {"allow_tools": ["one", "two"], "transport": "stdio"},
        "other": {"allow_tools": ["three"], "transport": "stdio"},
    }
    with pytest.raises(ValueError, match="select"):
        select_servers(config, [], {"teamlab": ["one"]})
    with pytest.raises(ValueError, match="Discover"):
        select_servers(config, ["other"], {"teamlab": ["one"]})
    selected = select_servers(config, ["teamlab"], {"teamlab": ["one"]})
    assert list(selected) == ["teamlab"]
    assert selected["teamlab"]["allow_tools"] == ["one"]
    assert "does not execute" in selection_summary(["teamlab"])
    assert "No simulator results retrieved" in activity_html()
    thinking = activity_html({"phase": "waiting_llm", "status": "running"})
    assert "Model thinking" in thinking and 'class="mcp-dial active"' not in thinking
    calling = activity_html({"phase": "tool_running", "status": "running", "tool_activity": {"current": "<bad>"}})
    assert 'class="mcp-dial active"' in calling and "&lt;bad&gt;" in calling
    state = {
        "status": "running",
        "phase": "waiting_simulator",
        "jobs": {"experiment123": {"status": "running", "progress": {"completed": 3, "total": 40}}},
    }
    assert "Simulator running" in activity_html(state)
    assert "3/40 policy runs" in activity_html(state)
    state["status"] = "failed"
    assert 'class="mcp-dial active"' not in activity_html(state)
    state["evidence"] = [{"tool": "teamlab__get_experiment_results"}]
    assert "Simulator results retrieved" in activity_html(state)


def test_experimental_question_uses_main_goal_unless_overridden():
    from app.experiment_ui import resolve_question

    assert resolve_question("", "Specialists versus generalists") == "Specialists versus generalists"
    assert resolve_question("  ", " Updated main goal ") == "Updated main goal"
    assert resolve_question(" Test switching cost ", "Main goal") == "Test switching cost"
    with pytest.raises(ValueError, match="Research Goal above"):
        resolve_question(" ", "")


@pytest.mark.parametrize("repair_design", [False, True])
def test_coordinated_workflow_discovers_then_plans_then_executes(api, tmp_path, repair_design):
    from app.experiment_ui import workflow_html

    agent = ExperimentAgent(configuration(api), "scripted", directory=tmp_path, coordinated=True)
    config = tiny()
    stages_seen = []

    async def complete(messages, tools):
        assert tools == []  # No model can submit a job during generation/planning/review.
        current = agent.state["workflow_stage"]
        stages_seen.append(current)
        context = json.loads(messages[1]["content"])
        if current == "hypotheses":
            assert "teamlab" in context["tools_and_limits"]
            assert context["tools_and_limits"]["teamlab"]["experiment_schema"]
            assert agent.state["jobs"] == {}
            payload = {
                "hypotheses": [
                    {
                        "id": "H1",
                        "claim": "Pooling lowers wait at zero setup.",
                        "mechanism": "Shared idle capacity",
                        "limitations": "Two synthetic task types; equal service rates.",
                    }
                ]
            }
        elif current == "questions":
            assert agent.state["stages"]["hypotheses"] == "completed"
            assert agent.state["jobs"] == {}
            payload = {
                "questions": [
                    {
                        "hypothesis_id": "H1",
                        "server": "TeamLab",
                        "question": "Does pooling lower mean wait?",
                        "prediction": "Flexible minus specialist mean wait < 0",
                        "metric": "cohort.mean_wait",
                        "falsification": "95% paired interval wholly above zero",
                        "config": config,
                    }
                ]
            }
            if repair_design and stages_seen.count("questions") == 1:
                bad = json.loads(json.dumps(config))
                bad["mode"] = "sweep"
                bad["sweep"] = {
                    "setup_delay": [0, 1, 5, 10],
                    "workload.service_a.cv": [0.1, 0.5, 1, 2],
                    "workload.service_b.cv": [0.1, 0.5, 1, 2],
                }
                for worker in bad["workers"]:
                    worker["eligibility"] = [worker["partition"]]
                payload["questions"][0]["config"] = bad
            elif repair_design:
                feedback = messages[-1]["content"]
                assert "two sweep axes" in feedback
                assert "budget is 120" in feedback
                assert "No worker can perform both tasks" in feedback
        else:
            assert current == "review"
            q = context["simulation_questions_and_evidence"][0]
            assert q["server"] == "teamlab"
            assert q["experiment_id"] and q["results"][0]["comparisons"]
            assert q["record_id"] and q["status"] == "evidence_collected"
            return {
                "role": "assistant",
                "content": "Measured results support a conditional claim; test switching costs next.",
            }
        return {"role": "assistant", "content": json.dumps(payload)}

    result = asyncio.run(agent.run("Compare specialist and generalist teams", complete))
    assert result["status"] == "completed", result.get("error")
    assert stages_seen == ["hypotheses", "questions"] + (["questions"] if repair_design else []) + ["review"]
    assert set(result["stages"].values()) == {"completed"}
    calls = [e["tool"] for e in result["events"] if "tool" in e]
    assert calls[:2] == ["teamlab__describe_environment", "teamlab__get_experiment_schema"]
    assert calls.index("teamlab__validate_experiment") < calls.index("teamlab__submit_experiment")
    page = workflow_html(result)
    assert "Hypotheses — not findings" in page and "Does pooling lower mean wait?" in page
    # Resume returns saved evidence without running the experiment again.
    resumed = ExperimentAgent(
        configuration(api), "scripted", directory=tmp_path, coordinated=True, session_id=result["id"]
    )
    assert asyncio.run(resumed.run("same goal", complete))["jobs"] == result["jobs"]
    assert len(stages_seen) == (4 if repair_design else 3)


def test_invalid_plan_never_submits(api, tmp_path):
    agent = ExperimentAgent(configuration(api), "scripted", directory=tmp_path, coordinated=True)

    async def complete(messages, tools):
        if agent.state["workflow_stage"] == "hypotheses":
            result = {"hypotheses": [{"id": "H1", "claim": "Test", "mechanism": "Pooling", "limitations": "Synthetic"}]}
        else:
            result = {
                "questions": [
                    {
                        "hypothesis_id": "H1",
                        "server": "teamlab",
                        "question": "Test?",
                        "prediction": "Less waiting",
                        "metric": "mean wait",
                        "falsification": "More waiting",
                        "config": {"nonexistent_proficiency": 0.8},
                    }
                ]
            }
        return {"role": "assistant", "content": json.dumps(result)}

    result = asyncio.run(agent.run("Question", complete))
    assert result["status"] == "failed"
    assert result["jobs"] == {} and result["submissions"] == {}
    assert "nonexistent_proficiency" in result["error"]
    assert "TaskGroup" not in result["error"]
    assert result["stages"]["questions"] == "failed"
    assert "experiments" not in result["stages"]


def test_http_errors_preserve_validation_detail(api):
    from teamlab.client import Client

    with pytest.raises(ValueError, match="TeamLab HTTP 422") as error:
        Client(api).request("/validate", {"unknown_field": True})
    assert "unknown_field" in str(error.value)


def test_taskgroup_errors_expose_redacted_leaf(monkeypatch):
    from app.experiment_agent import clean, error_message

    monkeypatch.setenv("ROUTER_API_KEY", "test-secret")
    error = ExceptionGroup("transport", [ExceptionGroup("nested", [ValueError("bad config test-secret")])])
    assert clean(error_message(error)) == "bad config [REDACTED]"


def test_server_id_resolution_only_uses_selected_servers():
    from app.research_workflow import resolve_server_id

    selected = {"teamlab": {}, "other": {}}
    assert resolve_server_id("teamlab", selected) == "teamlab"
    assert resolve_server_id(" TeamLab ", selected) == "teamlab"
    with pytest.raises(ValueError, match="exact selected server ID: other, teamlab"):
        resolve_server_id("unselected", selected)
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_server_id("TEAMLAB", {"TeamLab": {}, "teamlab": {}})
    assert resolve_server_id("TeamLab", {"TeamLab": {}, "teamlab": {}}) == "TeamLab"

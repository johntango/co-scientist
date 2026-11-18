"""Opt-in experimental research UI; connection configuration stays operator-owned."""

import asyncio
import html
import json
import os
import uuid

import gradio as gr

from .experiment_agent import ExperimentAgent, clean
from .mcp_tools import load_servers

ACTIVE = {}


def selection_summary(selected):
    names = ", ".join(selected or [])
    return (
        f"**Selected for the next run:** {names}. Selection makes tools available; it does not execute them."
        if names
        else "**No tools selected.** Discover servers, then select the tools you want available."
    )


def select_servers(configuration, selected, verified):
    if not selected or any(name not in verified or name not in configuration for name in selected):
        raise ValueError("Discover tools and select at least one available server before running research.")
    return {name: {**configuration[name], "allow_tools": verified[name]} for name in selected}


def workflow_html(state):
    from .research_workflow import STAGES

    def esc(value):
        return html.escape(str(value))

    statuses = state.get("stages", {})
    steps = "".join(
        f"<li><strong>{esc(label)}</strong> — {esc(statuses.get(key, 'pending'))}</li>" for key, label in STAGES
    )
    workflow = state.get("workflow", {})
    hypotheses = "".join(
        f"<li><strong>{esc(h['id'])}: {esc(h['claim'])}</strong><br>Mechanism: {esc(h['mechanism'])}"
        f"<br>Scope and limitations: {esc(h['limitations'])}</li>"
        for h in workflow.get("hypotheses", [])
    )
    questions = "".join(
        f"<li><strong>{esc(q['hypothesis_id'])}: {esc(q['question'])}</strong>"
        f"<br>Prediction (not a result): {esc(q['prediction'])}<br>Metric: {esc(q['metric'])}"
        f"<br>Falsification: {esc(q['falsification'])}<br>Tool server: {esc(q['server'])}"
        f" · Status: {esc(q['status'])} · Experiment: {esc(q.get('experiment_id', 'not submitted'))}"
        f"<br>Simulator outcome: {esc(q.get('experiment_status', 'not available'))}"
        f"<details><summary>Validated experiment configuration</summary><pre>{esc(json.dumps(q['config'], indent=2))}</pre></details></li>"
        for q in workflow.get("questions", [])
    )
    return (
        f'<section class="mcp-panel"><h3>Research workflow</h3><ol>{steps}</ol>'
        f"<h3>Hypotheses — not findings</h3><ul>{hypotheses or '<li>Awaiting tool discovery and hypothesis generation.</li>'}</ul>"
        f"<h3>Simulation questions</h3><ul>{questions or '<li>Awaiting completed hypotheses and validated plans.</li>'}</ul></section>"
    )


def activity_html(state=None):
    state = state or {}
    activity = state.get("tool_activity", {})
    phase = state.get("phase", "idle")
    terminal = state.get("status") in (
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "budget_exhausted",
        "needs_recovery",
        "no_evidence",
    )
    jobs = state.get("jobs", {})
    running = any(j["status"] == "running" for j in jobs.values()) and not terminal
    rpc = phase == "tool_running" and not terminal
    label = (
        "Simulator running"
        if running
        else "Calling tool"
        if rpc
        else {
            "waiting_llm": "Model thinking — no tool call in progress",
            "connecting": "Connecting and discovering — no experiment started",
            "waiting_simulator": "Waiting for simulator",
            "finished": "Research stopped",
        }.get(phase, "No tool call in progress")
    )
    if terminal:
        label = "Research " + state["status"].replace("_", " ")

    def esc(value):
        return html.escape(str(value))

    details = "".join(
        f"<li><code>{esc(name)}</code>: {counts['completed']} returned successfully, {counts['failed']} failed</li>"
        for name, counts in activity.get("tools", {}).items()
    )
    job_rows = "".join(
        f"<li>Experiment <code>{esc(ident)}</code>: {esc(job['status'])} "
        f"{esc(job.get('progress', {}).get('completed', 0))}/{esc(job.get('progress', {}).get('total', '?'))} policy runs</li>"
        for ident, job in jobs.items()
    )
    evidence = any(e.get("tool", "").endswith("__get_experiment_results") for e in state.get("evidence", []))
    evidence_label = (
        "Simulator results retrieved — inspect completion status and uncertainty below."
        if evidence
        else "No simulator results retrieved in this research session. Numerical model claims are unverified."
    )
    return f"""<style>
    @keyframes mcp-spin {{to {{transform:rotate(360deg)}}}}
    .mcp-panel {{border:1px solid #7b8b99;border-radius:12px;padding:16px;margin:8px 0}}
    .mcp-dial {{display:inline-block;width:22px;height:22px;border:3px solid #aab7bf;border-top-color:#087f8c;border-radius:50%;vertical-align:middle;margin-right:10px}}
    .mcp-dial.active {{animation:mcp-spin 1s linear infinite}}
    @media(prefers-reduced-motion:reduce) {{.mcp-dial.active {{animation:none}}}}
    </style><section class="mcp-panel" aria-label="Tool execution activity">
    <div role="status"><span aria-hidden="true" class="mcp-dial {"active" if running or rpc else ""}"></span><strong>{esc(label)}</strong></div>
    <p>Tool requests dispatched: {activity.get("started", 0)} · Successful: {activity.get("completed", 0)} · Failed: {activity.get("failed", 0)} · Experiments: {len(jobs)}</p>
    <p>Current tool: {esc(activity.get("current") or "None")}</p>
    <p><strong>{esc(evidence_label)}</strong></p><ul>{job_rows}</ul>
    <details><summary>Tools actually called (includes coordinator status checks)</summary><ul>{details or "<li>None yet</li>"}</ul></details>
    </section>{workflow_html(state)}"""


def resolve_question(override, main_goal):
    question = (override or "").strip() or (main_goal or "").strip()
    if not question:
        raise ValueError("Enter a Research Goal above, or an experimental question in this tab.")
    return question


def add_experiment_tab(source_getter=None, main_goal=None):
    with gr.Tab("Experimental Research (MCP)"):
        gr.Markdown(
            "One coordinated run: discover tools → generate hypotheses → design simulation questions → run simulations → evaluate evidence. "
            "You do not need to run the hypotheses-only cycle first. Runs and evidence are saved locally. "
            "Use a tool-capable OpenRouter model. LLM requests use your configured API key."
        )
        config_path = os.getenv("CO_SCIENTIST_MCP_CONFIG", "mcp_servers.json")
        try:
            configured = ", ".join(load_servers(config_path)) or "None"
        except Exception:
            configured = "Configuration unavailable"
        gr.Markdown(
            f"**Configured servers:** {configured}. Connection has not been checked.\n\n"
            "**1. Discover** available tools → **2. Select** a server's tools → **3. Run** research. "
            "The activity dial moves only during a tool call or confirmed simulator execution."
        )
        discovery_status = gr.Markdown("**Availability: not checked.** Click Discover configured tools.")
        selected_servers = gr.CheckboxGroup(
            choices=[], value=[], label="Make these servers’ tools available to the next research run"
        )
        verified = gr.State({})
        selection_status = gr.Markdown(selection_summary([]))
        source = gr.Dropdown(
            ["Generate hypotheses automatically", "Single Co-Scientist candidates", "Swarm candidates"],
            value="Generate hypotheses automatically",
            label="Hypothesis source",
        )
        if main_goal is None:
            main_goal = gr.State("")
        question = gr.Textbox(
            label="Experimental question (optional override)",
            lines=3,
            placeholder="Leave blank to use the Research Goal entered above.",
            info="Experimental research uses the main Research Goal unless you enter a different question here.",
        )
        model = gr.Textbox(label="Tool-capable OpenRouter model ID", value="openai/gpt-4.1-mini")
        with gr.Row():
            experiments = gr.Number(label="Maximum experiments", value=3, precision=0)
            runs = gr.Number(label="Maximum simulation runs", value=120, precision=0)
            calls = gr.Number(label="Maximum tool calls", value=32, precision=0)
        resume = gr.Textbox(label="Research session ID to resume (blank starts a new session)")
        token = gr.State(value=lambda: uuid.uuid4().hex)
        with gr.Row():
            discover_button = gr.Button("Discover configured tools")
            run_button = gr.Button("Run complete research workflow", variant="primary")
            cancel_button = gr.Button("Cancel research and simulator jobs", variant="stop")
        status = gr.Markdown()
        activity = gr.HTML(activity_html())
        answer = gr.Markdown()
        evidence = gr.JSON(label="Tool history, experiment status and evidence")
        tools = gr.JSON(label="Discovered tool schemas")

        async def discover_tools():
            schemas, available, outcomes = [], {}, []
            try:
                from .mcp_tools import ToolRegistry

                for name, spec in load_servers(config_path).items():
                    try:
                        async with ToolRegistry({name: spec}) as registry:
                            definitions = registry.definitions()
                        schemas.extend(definitions)
                        if definitions:
                            available[name] = [t["function"]["name"][len(name) + 2 :] for t in definitions]
                        outcomes.append(f"{name}: {len(definitions)} tools available")
                    except Exception as exc:
                        outcomes.append(f"{name}: unavailable ({clean(str(exc))})")
                return (
                    schemas,
                    gr.update(choices=list(available), value=[]),
                    available,
                    "**Discovery:** " + "; ".join(outcomes),
                    selection_summary([]),
                )
            except Exception as exc:
                return (
                    {"error": clean(str(exc))},
                    gr.update(choices=[], value=[]),
                    {},
                    "Discovery failed.",
                    selection_summary([]),
                )

        async def research(
            goal, selected, experiments, runs, calls, resume_id, token, source, selected_servers, verified, main_goal
        ):
            if token in ACTIVE:
                yield "Research is already running in this session.", "", {}, activity_html(ACTIVE[token].state)
                return
            task = None
            try:
                goal = resolve_question(goal, main_goal)
                agent = ExperimentAgent(
                    select_servers(load_servers(config_path), selected_servers, verified),
                    selected,
                    max_calls=int(calls),
                    max_experiments=int(experiments),
                    max_runs=int(runs),
                    session_id=resume_id.strip() or None,
                    coordinated=True,
                )
                ACTIVE[token] = agent
                candidates = (
                    source_getter(source) if source_getter and source != "Generate hypotheses automatically" else []
                )
                if source != "Generate hypotheses automatically" and not candidates:
                    raise ValueError("No candidates available from the selected pipeline. Run it first.")
                task = asyncio.create_task(
                    agent.evaluate_hypotheses(goal, candidates) if candidates else agent.run(goal)
                )
                while not task.done():
                    yield (
                        f"Session `{agent.state['id']}` — {agent.state['status']} · Tools enabled: {', '.join(agent.servers)}",
                        "",
                        clean(agent.state),
                        activity_html(agent.state),
                    )
                    await asyncio.sleep(0.5)
                result = await task
                yield (
                    f"Session `{result['id']}` — {result['status']}",
                    result.get("answer", result.get("error", "")),
                    result,
                    activity_html(result),
                )
            except Exception as exc:
                yield "Research could not complete.", clean(str(exc)), {}, activity_html({"status": "failed"})
            finally:
                if task is not None and not task.done():
                    agent.stop()
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                ACTIVE.pop(token, None)

        def cancel(token):
            agent = ACTIVE.get(token)
            if agent:
                agent.stop()
                return "Cancellation requested; waiting for simulator acknowledgement."
            return "No active research in this browser session. Resume a saved session to inspect its jobs."

        discover_button.click(
            discover_tools, outputs=[tools, selected_servers, verified, discovery_status, selection_status]
        )
        selected_servers.change(selection_summary, inputs=selected_servers, outputs=selection_status)
        run_button.click(
            research,
            inputs=[
                question,
                model,
                experiments,
                runs,
                calls,
                resume,
                token,
                source,
                selected_servers,
                verified,
                main_goal,
            ],
            outputs=[status, answer, evidence, activity],
        )
        cancel_button.click(cancel, inputs=token, outputs=status, queue=False)

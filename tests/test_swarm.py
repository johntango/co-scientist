"""Offline tests of swarm mode (app/swarm.py): the LLM boundary is mocked, no key and no network."""

import json

import app.swarm as swarm


def fake_llm(responses):
    """Route each prompt to a canned reply by the routine that wrote it."""
    calls = []

    def call(prompt, temperature=0.7, model=None):
        calls.append(prompt)
        if prompt.startswith("You are the orchestrator"):
            return json.dumps(
                [
                    {"name": f"D{i}", "objective": f"direction {i}", "data_types": "x", "boundaries": "y"}
                    for i in range(2)
                ]
            )
        if "You are worker" in prompt:
            w = 1 if "worker 1 of" in prompt else 2
            return json.dumps(
                [
                    {"title": f"W{w} idea {k}", "text": f"Worker {w} hypothesis number {k} about topic {w * 10 + k}."}
                    for k in range(3)
                ]
            )
        if prompt.startswith("You are a panel"):
            return responses.get(
                "judge", json.dumps({"winner": "A", "reason": "A is sharper", "against_B": "B is vague"})
            )
        if prompt.startswith("You are the meta-reviewer"):
            return json.dumps(
                {"critique": ["numbers unsupported"], "strengths": ["clear"], "guidance": ["cite the file"]}
            )
        if "You are the evolution agent" in prompt:
            return json.dumps({"title": "Improved idea", "text": "An improved hypothesis with a sharper design."})
        return "{}"

    return call, calls


def run(monkeypatch, **kw):
    call, calls = fake_llm(kw.pop("responses", {}))
    monkeypatch.setattr(swarm, "call_llm", call)
    monkeypatch.setattr(
        swarm,
        "call_llm_for_reflection",
        lambda text, temperature=0.5, model=None: {
            "novelty_review": "HIGH",
            "feasibility_review": "MEDIUM",
            "comment": "ok",
            "references": ["10.1000/x"],
        },
    )
    monkeypatch.setattr(swarm, "similarity_score", lambda a, b: 1.0 if a.split()[:3] == b.split()[:3] else 0.1)
    s = swarm.SwarmCoScientist(
        "Find hypotheses.",
        model="m",
        n_workers=2,
        per_worker=3,
        rounds=2,
        matches_per_round=2,
        top_k_evolve=2,
        final_k=4,
        parallel=2,
        **kw,
    )
    return s.run(), calls


def test_swarm_runs_all_routines(monkeypatch):
    res, calls = run(monkeypatch)
    roles = {t["role"] for t in res["trace"]}
    assert {"orchestrator", "worker1", "worker2", "reflection", "judge", "meta_review", "evolution"} <= roles
    assert len(res["lenses"]) == 2
    assert len(res["final"]) == 4
    assert any(h["parent_ids"] for h in res["hypotheses"])  # evolved hypotheses carry their parent
    assert len(res["meta_reviews"]) == 2


def test_elo_moves_and_is_conserved(monkeypatch):
    res, _ = run(monkeypatch)
    first_round = [h for h in res["hypotheses"] if h["round"] == 1 and h["active"]]
    assert any(h["elo"] != 1200.0 for h in first_round)
    assert all(h["wins"] + h["losses"] >= 1 for h in first_round)


def test_unparsable_judge_falls_back_and_is_reported(monkeypatch):
    res, _ = run(monkeypatch, responses={"judge": "I cannot decide."})
    assert any("judge: unparsable verdict" in e for e in res["errors"])
    assert res["matches"] and all(m["reason"].startswith("judge reply not parsable") for m in res["matches"])


def test_duplicates_are_merged_not_ranked(monkeypatch):
    res, _ = run(monkeypatch)
    # the fake similarity is 1.0 when the first three words match: "W1 idea 0" vs "W1 idea 1" share them
    assert res["merged"], "near-identical hypotheses must be merged"
    merged = {m["merged"] for m in res["merged"]}
    assert not any(hid in merged for hid in res["final"])


def test_parse_json_tolerates_fences_and_prose():
    assert swarm.parse_json('```json\n[{"a": 1}]\n```', list) == [{"a": 1}]
    assert swarm.parse_json('Here you go: {"winner": "B"} thanks', dict) == {"winner": "B"}
    assert swarm.parse_json("nothing", dict) is None


def test_format_results_html(monkeypatch):
    res, _ = run(monkeypatch)
    html = swarm.format_swarm_results(res)
    assert "Final ranking" in html and "Meta-review" in html

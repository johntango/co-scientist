"""Offline tests of uploaded data (app/data_context.py) and how both modes use it; no key and no network."""

import json

import app.swarm as swarm
from app.agents import GenerationAgent
from app.data_context import DataBundle, data_check_html, data_check_summary
from app.models import ContextMemory, ResearchGoal


def make_files(tmp_path, big_rows=3000):
    (tmp_path / "README.md").write_text(
        "Counts are rounded up to a multiple of 20.\nFitbit cohort: 68,820 participants.\n"
    )
    (tmp_path / "modality_counts.csv").write_text("flag,n_participants\nhas_fitbit,68820\nhas_ehr_data,413460\n")
    rows = ["concept_id,concept_name,n_participants"]
    rows += [f"{1000 + i},Filler condition {i},{20 * (i % 50 + 1)}" for i in range(big_rows)]
    rows.append("313217,Atrial fibrillation,12340")
    rows.append("4329847,Myocardial infarction,8800")
    (tmp_path / "concept_counts.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "notes.xlsx").write_bytes(b"not supported")
    return tmp_path


def test_loading_and_summary(tmp_path):
    d = DataBundle.from_dir(str(make_files(tmp_path)))
    assert [f.name for f in d.files] == ["README.md", "concept_counts.csv", "modality_counts.csv"]
    assert "notes.xlsx: unsupported type" in d.summary()
    assert DataBundle.from_paths([]).files == [] and not DataBundle.from_paths([])


def test_brief_fits_budget_keeps_small_tables_and_picks_relevant_rows(tmp_path):
    d = DataBundle.from_dir(str(make_files(tmp_path)))
    b = d.brief("atrial fibrillation and fitbit heart rhythm", budget=6000)
    assert len(b) <= 6000 + 200
    assert "no sandbox" in b and "68,820 participants" in b  # preamble and README
    assert "has_fitbit,68820" in b  # small table whole
    assert "313217,Atrial fibrillation,12340" in b  # the relevant row of the big table
    assert "showing" in b and "of 3,002 rows" in b
    b2 = d.brief("myocardial infarction", budget=6000)
    assert "4329847,Myocardial infarction,8800" in b2


def test_number_check(tmp_path):
    d = DataBundle.from_dir(str(make_files(tmp_path)))
    text = "Cohort: 68 820 Fitbit users with AF (12,340 in concept_counts); about 55,000 had labs in 2021; 8800 MI."
    res = {c["number"]: c for c in d.check_numbers(text)}
    assert res["68,820"]["found"] and any("modality_counts.csv" in w for w in res["68,820"]["where"])
    assert res["12,340"]["found"] and "concept_counts.csv" in res["12,340"]["where"][0]
    assert not res["55,000"]["found"]
    assert "2021" not in res  # years left out
    assert res["8800"]["found"]
    assert data_check_summary(list(res.values())) == "3 of 4 quoted numbers found in the uploaded data"
    assert "not in the uploaded data" in data_check_html(list(res.values()))


def test_single_mode_generation_prompt_carries_the_extract(tmp_path, monkeypatch):
    import app.agents as agents

    seen = []

    def fake(prompt, num_hypotheses=3, temperature=0.7, model=None):
        seen.append(prompt)
        return [{"title": "t", "text": "x"}]

    monkeypatch.setattr(agents, "call_llm_for_generation", fake)
    d = DataBundle.from_dir(str(make_files(tmp_path)))
    GenerationAgent().generate_new_hypotheses(
        ResearchGoal("Atrial fibrillation in Fitbit users", data=d), ContextMemory()
    )
    GenerationAgent().generate_new_hypotheses(ResearchGoal("Atrial fibrillation in Fitbit users"), ContextMemory())
    assert "ATTACHED DATA" in seen[0] and "313217,Atrial fibrillation,12340" in seen[0]
    assert "ATTACHED DATA" not in seen[1]  # unchanged without data


def test_swarm_with_data(tmp_path, monkeypatch):
    prompts = []

    def call(prompt, temperature=0.7, model=None):
        prompts.append(prompt)
        if prompt.startswith("You are the orchestrator"):
            return json.dumps(
                [{"name": "AF", "objective": "atrial fibrillation", "data_types": "fitbit", "boundaries": "-"}]
            )
        if "You are worker" in prompt:
            return json.dumps(
                [
                    {"title": "AF idea", "text": "AF in 12,340 participants and 999,999 others."},
                    {"title": "Second", "text": "68,820 Fitbit users."},
                ]
            )
        if prompt.startswith("You are a panel"):
            return json.dumps({"winner": "A", "reason": "r", "against_B": "b"})
        if prompt.startswith("You are the meta-reviewer"):
            return json.dumps({"critique": [], "strengths": [], "guidance": []})
        return "{}"

    monkeypatch.setattr(swarm, "call_llm", call)
    monkeypatch.setattr(
        swarm, "call_llm_for_reflection", lambda text, temperature=0.5, model=None: {"comment": "ok", "references": []}
    )
    monkeypatch.setattr(swarm, "similarity_score", lambda a, b: 0.0)
    d = DataBundle.from_dir(str(make_files(tmp_path)))
    res = swarm.SwarmCoScientist(
        "Find AF hypotheses.", n_workers=1, per_worker=2, rounds=1, data=d, data_budget=5000
    ).run()
    worker = next(p for p in prompts if "You are worker" in p)
    judge = next(p for p in prompts if p.startswith("You are a panel"))
    assert "313217,Atrial fibrillation,12340" in worker and "ATTACHED DATA" in judge
    by = {h["id"]: h for h in res["hypotheses"]}
    checks = {c["number"]: c["found"] for h in by.values() for c in h["data_check"]}
    assert checks == {"12,340": True, "999,999": False, "68,820": True}
    assert "Uploaded data" in swarm.format_swarm_results(res) and "Data check" in swarm.format_swarm_results(res)
    assert res["settings"]["data"].startswith("3 file(s)")


def test_swarm_prompts_unchanged_without_data(monkeypatch):
    s = swarm.SwarmCoScientist("goal")
    assert s.data_block("anything") == "" and s.data is None

"""Regression tests: every swarm export carries the full direction assigned to each worker.

Directions come from the run's stored assignments (result["lenses"], the records the live "Directions" list shows),
are joined to ideas by worker ID only, and are written to share/directions.json, the bundle's Directions section, each
idea in ideas_bundle.json, and each idea_rankNN.html. Single-mode and older runs still export, with null directions.
"""

import html
import importlib.util
import json
import os
import zipfile

import pytest

from app.export import directions_from_lenses, ideas_from_hypotheses, write_bundle

# worker 3 produces no ranked idea; workers 1 and 4 share a lens label; direction 2 has punctuation and HTML characters
LENSES = [
    {
        "name": "WaterSupply",
        "objective": "Compare households by water company; use deaths per house.",
        "data_types": "x",
    },
    {
        "name": "SocialSpatialPattern",
        "objective": 'Test <b>poverty</b> & crowding ("same street") vs. elevation — 1849 → 1854; don\'t use <script>.',
        "data_types": "y",
    },
    {
        "name": "TemporalIntervention",
        "objective": "Onsets before/after the 8 Sept pump-handle removal.",
        "data_types": "z",
    },
    {"name": "WaterSupply", "objective": "Trace the 1852 intake move up-river (Thames Ditton).", "data_types": "w"},
]


def hyp(hid, worker, elo):
    return {
        "id": hid,
        "title": f"Idea {hid}",
        "text": f"Text of {hid}.",
        "elo": elo,
        "worker": worker,
        "lens": LENSES[worker - 1]["name"],
        "wins": 1,
        "losses": 0,
        "review_comments": [],
        "references": [],
        "parent_ids": [],
    }


# ranks interleave workers: 2, 1, 4, 2, 1 (worker 3 absent)
FINAL = [hyp("S001", 1, 1210), hyp("S002", 2, 1250), hyp("S003", 4, 1230), hyp("S004", 2, 1220), hyp("S005", 1, 1190)]
EXPECTED = {"S002": 2, "S001": 1, "S003": 4, "S004": 2, "S005": 1}


def unzip(zpath, tmp_path):
    out = tmp_path / "unzipped"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    (root,) = os.listdir(out)
    return out / root


def test_directions_from_lenses_keeps_order_and_exact_text():
    d = directions_from_lenses(LENSES)
    assert [x["worker"] for x in d] == [1, 2, 3, 4]
    assert [x["lens"] for x in d] == [x["name"] for x in LENSES]
    assert [x["direction"] for x in d] == [x["objective"] for x in LENSES]
    assert set(d[0]) == {"worker", "lens", "direction"}
    assert directions_from_lenses(None) == [] and directions_from_lenses([]) == []


def test_swarm_export_carries_every_direction(tmp_path):
    ideas = ideas_from_hypotheses(FINAL)
    z = write_bundle(
        ideas, str(tmp_path / "run"), "run", "goal", mode="swarm", directions=directions_from_lenses(LENSES)
    )
    root = unzip(z, tmp_path)

    # manifest: every assignment, in order, including worker 3 with no ranked idea
    manifest = json.loads((root / "share" / "directions.json").read_text())
    assert manifest == [{"worker": k, "lens": x["name"], "direction": x["objective"]} for k, x in enumerate(LENSES, 1)]

    # ideas_bundle.json: still a top-level array with the old fields, plus the right direction per idea
    bundle = json.loads((root / "share" / "ideas_bundle.json").read_text())
    assert isinstance(bundle, list) and [i["id"] for i in bundle] == ["S002", "S003", "S004", "S001", "S005"]
    for i in bundle:
        assert {"rank", "id", "title", "elo_rating", "worker", "lens", "review"} <= set(i)
        assert i["direction"] == LENSES[EXPECTED[i["id"]] - 1]["objective"]
    # workers 1 and 4 share the lens label but keep their own directions
    by = {i["id"]: i for i in bundle}
    assert by["S001"]["lens"] == by["S003"]["lens"] and by["S001"]["direction"] != by["S003"]["direction"]

    # Markdown: Directions section directly above the leaderboard, in assignment order, text exact
    md = (root / "share" / "ideas_bundle.md").read_text()
    sec = md.index("## Directions")
    assert sec < md.index("## Leaderboard") and "## " not in md[sec + 3 : md.index("## Leaderboard")]
    positions = [md.index(f"- **Worker {k} · {x['name']}**: {x['objective']}") for k, x in enumerate(LENSES, 1)]
    assert positions == sorted(positions)
    for hid, w in EXPECTED.items():
        block = md[md.index(f"`{hid}`") :]
        assert (
            block.split("\n## Summary")[0]
            .rstrip()
            .endswith(f"(Worker {w} · {LENSES[w - 1]['name']}): {LENSES[w - 1]['objective']}")
        )

    # HTML bundle: Directions table above the leaderboard, escaped, in order
    bh = (root / "share" / "ideas_bundle.html").read_text()
    assert bh.index("<h2>Directions</h2>") < bh.index("<h2>Leaderboard</h2>")
    rows = [
        bh.index(f"<td>{k}</td><td>{html.escape(x['name'])}</td><td>{html.escape(x['objective'])}</td>")
        for k, x in enumerate(LENSES, 1)
    ]
    assert rows == sorted(rows)
    assert "<b>poverty</b>" not in bh and "use <script>." not in bh  # direction text is escaped, never markup
    assert "&lt;b&gt;poverty&lt;/b&gt;" in bh and "use &lt;script&gt;." in bh

    # each idea page shows its own direction beside its worker and lens
    for i in bundle:
        page = (root / "share" / f"idea_rank{i['rank']:02d}.html").read_text()
        w = EXPECTED[i["id"]]
        assert (
            f"(Worker {w} · {html.escape(LENSES[w - 1]['name'])}): {html.escape(LENSES[w - 1]['objective'])}</p>"
            in page
        )
        others = [x["objective"] for k, x in enumerate(LENSES, 1) if k != w]
        assert not any(html.escape(o) in page for o in others)


def test_single_mode_export_has_no_directions(tmp_path):
    hyps = [{"id": "G1", "title": "T", "text": "x", "elo_score": 1200, "references": [], "review_comments": []}]
    root = unzip(write_bundle(ideas_from_hypotheses(hyps), str(tmp_path / "s"), "s", "goal"), tmp_path)
    assert json.loads((root / "share" / "directions.json").read_text()) == []
    assert json.loads((root / "share" / "ideas_bundle.json").read_text())[0]["direction"] is None
    assert "Directions" not in (root / "share" / "ideas_bundle.md").read_text()
    assert 'class="direction"' not in (root / "share" / "idea_rank01.html").read_text()


def test_legacy_swarm_export_without_stored_directions(tmp_path):
    root = unzip(
        write_bundle(ideas_from_hypotheses(FINAL), str(tmp_path / "old"), "old", "goal", mode="swarm"), tmp_path
    )
    bundle = json.loads((root / "share" / "ideas_bundle.json").read_text())
    assert (
        all(i["direction"] is None for i in bundle)
        and json.loads((root / "share" / "directions.json").read_text()) == []
    )
    assert "Directions" not in (root / "share" / "ideas_bundle.md").read_text()
    assert all(
        'class="direction"' not in (root / "share" / f"idea_rank{i['rank']:02d}.html").read_text() for i in bundle
    )


@pytest.fixture(scope="module")
def app_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("gradio_app_dirs", os.path.join(repo_root, "app.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_export_uses_the_runs_stored_lenses(app_module, tmp_path, monkeypatch):
    """The live page and the ZIP read the same stored records; exporting makes no model call."""
    monkeypatch.setattr(app_module, "get_reports_dir", lambda: tmp_path)
    monkeypatch.setattr("app.swarm.call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    res = {
        "lenses": LENSES,
        "hypotheses": FINAL,
        "final": [h["id"] for h in FINAL],
        "matches": [],
        "settings": dict(n_workers=4, per_worker=2, rounds=1, data=None),
    }
    app_module.LAST_SWARM.clear()
    app_module.LAST_SWARM.update(res=res, goal="goal", model="m")
    root = unzip(app_module.export_bundle_zip("Swarm"), tmp_path)
    manifest = json.loads((root / "share" / "directions.json").read_text())
    live = app_module.format_swarm_results(dict(res, errors=[], trace=[], meta_reviews=[], seconds=1))
    # every manifest record appears, in order, in the live page's Directions list
    spots = [live.index(f"<b>{html.escape(d['lens'])}</b>: {html.escape(d['direction'])}") for d in manifest]
    assert spots == sorted(spots) and len(manifest) == 4

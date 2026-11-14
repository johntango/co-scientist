"""Offline tests of Export results (app/export.py)."""

import json
import os
import zipfile

from app.export import ideas_from_hypotheses, summary_of, write_bundle

HYPS = [
    {
        "id": "G1",
        "title": "Sleep and mood",
        "text": "**Hypothesis**: Short sleep predicts low mood.\n\n**Cohort**: 1,000 people.",
        "elo_score": 1210.0,
        "novelty_review": "HIGH",
        "feasibility_review": "MEDIUM",
        "review_comments": ["Clear and testable."],
        "references": ["Smith J. Sleep. 2020. doi:10.1000/xyz123"],
        "parent_ids": [],
    },
    {
        "id": "E2",
        "title": "Combined",
        "text": "Combination of:\n1. A\n2. B",
        "elo_score": 1250.0,
        "novelty_review": "LOW",
        "feasibility_review": "HIGH",
        "review_comments": [],
        "references": [],
        "parent_ids": ["G1"],
    },
]


def test_export_layout(tmp_path):
    out = tmp_path / "run_x"
    z = write_bundle(ideas_from_hypotheses(HYPS), str(out), "run_x", "Find hypotheses.", mode="single", model="m")
    for f in (
        "share/ideas_bundle.json",
        "share/ideas_bundle.md",
        "share/ideas_bundle.html",
        "share/idea_rank01.html",
        "share/idea_rank02.html",
        "reviews/review_G1.md",
        "reviews/review_E2.md",
        "references.csv",
        "references.md",
    ):
        assert (out / f).exists(), f
    names = zipfile.ZipFile(z).namelist()
    assert "run_x/share/ideas_bundle.html" in names


def test_ranked_by_elo_with_bundle_keys(tmp_path):
    out = tmp_path / "r"
    write_bundle(ideas_from_hypotheses(HYPS), str(out), "r", "goal")
    data = json.load(open(out / "share" / "ideas_bundle.json"))
    assert [d["id"] for d in data] == ["E2", "G1"]
    for k in (
        "rank",
        "id",
        "title",
        "summary",
        "text",
        "elo_rating",
        "review",
        "references",
        "review_score",
        "tools_used",
        "goal",
        "run_dir",
    ):
        assert k in data[0]
    assert data[1]["references"][0]["doi"] == "10.1000/xyz123"
    html = open(out / "share" / "idea_rank01.html").read()
    assert 'href="ideas_bundle.html"' in html and "Evolved from" in open(out / "reviews" / "review_E2.md").read()


def test_swarm_debates_in_review(tmp_path):
    hs = [
        dict(h, elo=h["elo_score"], wins=1 if h["id"] == "E2" else 0, losses=0 if h["id"] == "E2" else 1) for h in HYPS
    ]
    matches = [{"winner": "E2", "loser": "G1", "reason": "E2 is sharper", "round": 1, "match": 1}]
    ideas = ideas_from_hypotheses(hs, matches)
    assert "Tournament (1 wins, 0 losses)" in ideas[0]["review"] and "E2 is sharper" in ideas[0]["review"]


def test_summary_uses_hypothesis_sentence():
    assert summary_of(HYPS[0]["text"]) == "Short sleep predicts low mood."
    assert os.path.basename("x") == "x"

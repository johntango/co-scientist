"""Offline tests of reply handling: throttled models are retried, tiny models are never fallbacks, empty replies are
retried, unreadable or cut-off JSON is retried or salvaged, and a swarm with no hypotheses stops cleanly."""

import json
from unittest.mock import MagicMock, patch

import app.agents as agents
import app.swarm as swarm
import app.utils as utils
from app.utils import call_llm


def _completion(text):
    c = MagicMock()
    c.choices = [MagicMock()]
    c.choices[0].message.content = text
    return c


def _client(behavior):
    client = MagicMock()
    client.chat.completions.create.side_effect = lambda model, messages, temperature: behavior(model)
    return client


def test_rate_limited_primary_is_retried_before_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    calls = []

    def behavior(model):
        calls.append(model)
        if model == "big:free" and calls.count("big:free") == 1:
            raise Exception("Error code: 429 - temporarily rate-limited upstream")
        return _completion(f"reply from {model}")

    with patch.object(utils, "OpenAI", return_value=_client(behavior)):
        out = call_llm("p", model="big:free", fallback_models=["other:free"])
    assert out == "reply from big:free" and calls == ["big:free", "big:free"]
    assert utils.last_model_used() == "big:free"


def test_tiny_models_are_never_fallbacks():
    with patch.object(
        utils,
        "fetch_free_models",
        return_value=["liquid/lfm-3-1.7b:free", "google/gemma-4-26b-a4b-it:free", "x/unknown:free"],
    ):
        fb = utils.get_fallback_models("google/gemma-4-31b-it:free")
    assert fb == ["google/gemma-4-26b-a4b-it:free", "x/unknown:free"]


def test_empty_reply_is_retried_then_falls_back(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.setattr(utils, "RATE_LIMIT_WAITS", [])

    def behavior(model):
        return _completion("" if model == "a:free" else "ok")

    with patch.object(utils, "OpenAI", return_value=_client(behavior)):
        assert call_llm("p", model="a:free", fallback_models=["b:free"]) == "ok"
    assert utils.last_model_used() == "b:free"


def test_parse_json_salvages_cut_off_array_and_control_characters():
    cut = '[{"title": "A", "text": "one"}, {"title": "B", "text": "two"}, {"title": "C", "text": "thr'
    assert [x["title"] for x in swarm.parse_json(cut, list)] == ["A", "B"]
    raw = 'Here you go:\n[{"title": "T", "text": "line1\nline2"}]\nThanks'
    assert swarm.parse_json(raw, list)[0]["text"] == "line1\nline2"
    assert swarm.reply_snippet("") == "empty reply"


def test_single_mode_generation_retries_once(monkeypatch):
    replies = iter(["Sure! Let me think about this...", json.dumps([{"title": "T", "text": {"a": 1}}])])
    monkeypatch.setattr(agents, "call_llm", lambda prompt, temperature=0.7, model=None: next(replies))
    out = agents.call_llm_for_generation("goal", 1)
    assert out[0]["title"] == "T" and '"a": 1' in out[0]["text"]


def test_single_mode_generation_reports_reply_start(monkeypatch):
    monkeypatch.setattr(agents, "call_llm", lambda prompt, temperature=0.7, model=None: "")
    out = agents.call_llm_for_generation("goal", 1)
    assert out[0]["title"] == "Error" and "empty reply" in out[0]["text"]


def test_swarm_with_no_hypotheses_stops_cleanly(monkeypatch):
    monkeypatch.setattr(swarm, "call_llm", lambda prompt, temperature=0.7, model=None: "")
    res = swarm.SwarmCoScientist("goal", n_workers=2, per_worker=2, rounds=1).run()
    assert res["final"] == [] and res["matches"] == [] and res["meta_reviews"] == []
    assert any("no hypotheses were generated" in e for e in res["errors"])
    assert any("empty reply" in e for e in res["errors"] if e.startswith("worker"))
    assert "No hypotheses were generated" in swarm.format_swarm_results(res)


def test_slow_call_hits_the_hard_cap(monkeypatch):
    import time as _t

    monkeypatch.setattr(utils, "LLM_CALL_MAX_SECONDS", 0.2)
    try:
        utils._with_deadline(lambda: _t.sleep(2), 0.2)
    except TimeoutError as e:
        assert "timed out" in str(e) and utils._is_timeout(str(e))
    else:
        raise AssertionError("no timeout")
    assert utils._with_deadline(lambda: 7, 1) == 7


def test_reasoning_models_are_tried_last():
    with patch.object(
        utils, "fetch_free_models", return_value=["n/nemotron-30b-reasoning:free", "g/gemma-4-31b:free", "x/flash:free"]
    ):
        assert utils.get_fallback_models("p:free") == [
            "g/gemma-4-31b:free",
            "x/flash:free",
            "n/nemotron-30b-reasoning:free",
        ]

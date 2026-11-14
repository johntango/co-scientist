"""Shared test settings: no real waiting between rate-limit retries."""

import pytest

import app.utils as utils


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    monkeypatch.setattr(utils, "_sleep", lambda s: None)

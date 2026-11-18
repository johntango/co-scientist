"""Dependency-free Python HTTP client. Errors include the API's structured body."""

import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Client:
    def __init__(self, base_url="http://127.0.0.1:8000"):
        self.base = base_url.rstrip("/") + "/api/v1"

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"TeamLab HTTP {exc.code}: {detail}") from None

    def submit(self, config):
        return self.request("/experiments", config)

    def wait(self, ident, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.request(f"/experiments/{ident}")
            if state["status"] not in ("queued", "running", "cancelling"):
                return state
            time.sleep(0.2)
        raise TimeoutError("Experiment still pending; inspect status or cancel explicitly")

    def results(self, ident):
        return self.request(f"/experiments/{ident}/results")

    def cancel(self, ident):
        return self.request(f"/experiments/{ident}/cancel", {})

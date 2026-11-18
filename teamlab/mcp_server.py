"""MCP adapter: the HTTP service remains the sole simulation executor."""

import argparse
import re

from mcp.server.fastmcp import FastMCP

from .client import Client
from .models import Experiment, ResearchRecord, WorkloadRequest


def create_server(base_url="http://127.0.0.1:8000"):
    client = Client(base_url)
    server = FastMCP("TeamLab", host="127.0.0.1", port=8001)

    def ident(value):
        if not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("Expected an experiment ID (32 hexadecimal characters)")
        return value

    @server.tool()
    def describe_environment() -> dict:
        """Discover simulation assumptions, metrics, policies, and execution limits."""
        return client.request("/environment")

    @server.tool()
    def get_experiment_schema() -> dict:
        """Get the complete JSON schema for experiment configurations."""
        return client.request("/schema")

    @server.tool()
    def validate_experiment(config: Experiment) -> dict:
        """Validate configuration and compute run count without executing."""
        return client.request("/validate", config.model_dump())

    @server.tool()
    def record_prediction(record: ResearchRecord) -> dict:
        """Persist a falsifiable prediction BEFORE submission. Revisions use parent_id."""
        return client.request("/research-records", record.model_dump())

    @server.tool()
    def generate_workload(request: WorkloadRequest) -> dict:
        """Generate and persist a reproducible exogenous workload; returns hash and metadata."""
        return client.request("/workloads", request.model_dump())

    @server.tool()
    def submit_experiment(config: Experiment, request_key: str) -> dict:
        """Submit once and return job ID immediately. Reuse request_key for retries, never new work.

        Requires research_record_id from record_prediction. Poll status to completion.
        """
        if not config.research_record_id:
            raise ValueError("Record a prediction and set research_record_id before submission")
        if not 1 <= len(request_key) <= 128:
            raise ValueError("request_key must contain 1–128 characters")
        return client.request("/experiments", config.model_dump(), {"Idempotency-Key": request_key})

    @server.tool()
    def get_experiment_status(experiment_id: str) -> dict:
        """Read durable progress/status/config/provenance, including interrupted and failed jobs."""
        return client.request(f"/experiments/{ident(experiment_id)}")

    @server.tool()
    def get_experiment_results(experiment_id: str, cell: int = 0) -> dict:
        """Read one bounded result cell: paired intervals and replication metrics/hash/status.

        Jobs, queue traces and plots remain in downloadable artifacts. Iterate cell indexes.
        Undefined/incomplete metrics must not be treated as successful evidence.
        """
        result = client.results(ident(experiment_id))
        if not 0 <= cell < len(result["cells"]):
            raise ValueError("Cell index out of range")
        selected = result["cells"][cell]
        runs = [
            {
                k: v
                for k, v in r.items()
                if k in ("seed", "policy", "workload_hash", "status", "cohort", "warnings", "window")
            }
            for r in selected["runs"]
        ]
        return {
            "experiment_id": experiment_id,
            "cell": cell,
            "cell_count": len(result["cells"]),
            "parameters": selected["parameters"],
            "comparisons": selected["comparisons"],
            "runs": runs,
            "warnings": result["warnings"],
        }

    @server.tool()
    def cancel_experiment(experiment_id: str) -> dict:
        """Cancel the actual simulator job; cancellation is cooperative."""
        return client.cancel(ident(experiment_id))

    @server.tool()
    def get_artifacts(experiment_id: str) -> dict:
        """Return links to full JSON, CSV, workload, provenance and plot artifacts."""
        return {"artifacts": client.request(f"/experiments/{ident(experiment_id)}/artifacts"), "base_url": base_url}

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    args = parser.parse_args()
    create_server(args.base_url).run(transport=args.transport)


if __name__ == "__main__":
    main()

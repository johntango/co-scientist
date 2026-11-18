"""Local-only versioned HTTP interface. Run through `teamlab serve`."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .environment import describe
from .models import Experiment, ResearchRecord, WorkloadRequest
from .runner import BusyError, Runner, artifact_dir, artifacts, results
from .store import Store
from .workload import LimitExceeded, generate


def create_app(directory=None, limits=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.runner = Runner(Store(directory), limits)
        yield
        app.state.runner.close()

    app = FastAPI(title="TeamLab research API", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def bound_requests(request: Request, call_next):
        if request.method == "POST" and len(await request.body()) > 1_000_000:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "request_too_large", "message": "Request body limit is 1 MB"}},
            )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": str(exc)}})

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        status = 429 if isinstance(exc, BusyError) else 422 if isinstance(exc, LimitExceeded) else 400
        return JSONResponse(status_code=status, content={"error": {"code": type(exc).__name__, "message": str(exc)}})

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": str(exc)}})

    @app.get("/api/v1/environment")
    def environment():
        return describe(app.state.runner.limits)

    @app.get("/api/v1/schema")
    def schema():
        return Experiment.model_json_schema()

    @app.post("/api/v1/workloads", status_code=201)
    def create_workload(request: WorkloadRequest):
        runner = app.state.runner
        return runner.store.save_workload(generate(request.config, request.seed, runner.limits.max_jobs))

    @app.get("/api/v1/workloads/{ident}")
    def workload(ident: str):
        return app.state.runner.store.workload(ident)

    @app.get("/api/v1/workloads/{ident}/download")
    def download_workload(ident: str):
        store = app.state.runner.store
        meta = store.workload(ident)
        return FileResponse(
            store.root / "workloads" / f"{meta['id']}.json",
            media_type="application/json",
            filename=f"workload-{ident}.json",
        )

    @app.post("/api/v1/validate")
    def validate(config: Experiment):
        cells = app.state.runner.validate(config)
        return {"valid": True, "runs": len(cells) * len(config.seeds) * len(config.policies)}

    @app.post("/api/v1/experiments", status_code=202)
    def submit(config: Experiment, idempotency_key: str | None = Header(default=None, max_length=128)):
        return app.state.runner.submit(config, idempotency_key)

    @app.get("/api/v1/experiments/{ident}")
    def status(ident: str):
        return app.state.runner.store.experiment(ident)

    @app.get("/api/v1/experiments/{ident}/results")
    def get_results(ident: str):
        return results(app.state.runner.store, ident)

    @app.get("/api/v1/experiments/{ident}/artifacts")
    def get_artifacts(ident: str):
        return artifacts(app.state.runner.store, ident)

    @app.get("/api/v1/experiments/{ident}/artifacts/{name}")
    def download(ident: str, name: str):
        store = app.state.runner.store
        allowed = {a["name"] for a in artifacts(store, ident) if a["url"].startswith(f"/api/v1/experiments/{ident}/")}
        if name not in allowed:
            raise KeyError("Unknown artifact")
        return FileResponse(artifact_dir(store, ident) / name, filename=name)

    @app.post("/api/v1/experiments/{ident}/cancel")
    def cancel(ident: str):
        return app.state.runner.cancel(ident)

    @app.post("/api/v1/research-records", status_code=201)
    def record(request: ResearchRecord):
        return app.state.runner.store.add_record(request)

    @app.get("/api/v1/research-records")
    def records():
        return app.state.runner.store.records()

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(static / "index.html")

    return app

"""SQLite metadata and content-addressed, immutable workload artifacts."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .workload import canonical, digest


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory=None):
        self.root = Path(directory or os.getenv("TEAMLAB_DATA_DIR", "teamlab-data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "workloads").mkdir(exist_ok=True)
        (self.root / "experiments").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workloads(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submissions(key TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "teamlab.sqlite3", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def workload(self, ident, full=False):
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM workloads WHERE id=?", (ident,)).fetchone()
        if not row:
            raise KeyError("Unknown workload")
        meta = json.loads(row[0])
        if not full:
            return meta
        data = json.loads((self.root / "workloads" / f"{meta['id']}.json").read_text())
        body = {k: v for k, v in data.items() if k not in ("id", "hash")}
        if digest(body) != meta["hash"]:
            raise ValueError("Workload integrity check failed")
        return data

    def save_workload(self, workload):
        body = {k: v for k, v in workload.items() if k not in ("id", "hash")}
        if digest(body) != workload["hash"] or workload["id"] != workload["hash"]:
            raise ValueError("Workload integrity check failed")
        path = self.root / "workloads" / f"{workload['id']}.json"
        # Publish a fully written file atomically without overwriting an existing hash.
        temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(canonical(workload))
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if json.loads(path.read_text()) != workload:
                    raise ValueError("Immutable workload collision") from None
        finally:
            temporary.unlink(missing_ok=True)
        meta = {k: v for k, v in workload.items() if k != "jobs"}
        meta.update(
            job_count=len(workload["jobs"]),
            created_at=timestamp(),
            download=f"/api/v1/workloads/{workload['id']}/download",
        )
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO workloads VALUES (?,?)", (workload["id"], canonical(meta)))
        return self.workload(workload["id"])

    def submission(self, key, config):
        with self.connect() as db:
            row = db.execute("SELECT experiment_id, config FROM submissions WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if row[1] != canonical(config):
            raise ValueError("Idempotency key already used with a different configuration")
        return self.experiment(row[0])

    def create_experiment(self, config, provenance, request_key=None):
        ident = uuid.uuid4().hex
        body = {
            "id": ident,
            "status": "queued",
            "progress": {"completed": 0, "total": 0},
            "config": config,
            "provenance": provenance,
            "created_at": timestamp(),
            "started_at": None,
            "finished_at": None,
            "warnings": [],
            "error": None,
        }
        with self.connect() as db:
            db.execute("INSERT INTO experiments VALUES (?,?,?)", (ident, "queued", canonical(body)))
            if request_key:
                db.execute("INSERT INTO submissions VALUES (?,?,?)", (request_key, ident, canonical(config)))
        (self.root / "experiments" / ident).mkdir()
        return body

    def experiment(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT body FROM experiments WHERE id=?", (ident,)).fetchone()
        if not row:
            raise KeyError("Unknown experiment")
        return json.loads(row[0])

    def update(self, ident, **values):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM experiments WHERE id=?", (ident,)).fetchone()
            if not row:
                raise KeyError("Unknown experiment")
            body = json.loads(row[0])
            body.update(values)
            db.execute("UPDATE experiments SET status=?, body=? WHERE id=?", (body["status"], canonical(body), ident))
        return body

    def interrupted(self):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM experiments WHERE status IN ('queued','running','cancelling')").fetchall()
        for (ident,) in rows:
            self.update(
                ident,
                status="interrupted",
                finished_at=timestamp(),
                error={
                    "code": "process_interrupted",
                    "message": "Executor stopped before completion. Resubmit the saved configuration to reproduce.",
                },
            )

    def records(self):
        with self.connect() as db:
            rows = db.execute("SELECT body FROM research ORDER BY rowid DESC").fetchall()
            experiments = [json.loads(r[0]) for r in db.execute("SELECT body FROM experiments").fetchall()]
        records = [json.loads(r[0]) for r in rows]
        for record in records:
            record["experiments"] = [
                {"id": e["id"], "status": e["status"], "created_at": e["created_at"]}
                for e in experiments
                if e["config"].get("research_record_id") == record["id"]
            ]
        return records

    def add_record(self, record):
        if record.parent_id and not any(r["id"] == record.parent_id for r in self.records()):
            raise KeyError("Unknown parent research record")
        body = {"id": uuid.uuid4().hex, "created_at": timestamp(), **record.model_dump()}
        with self.connect() as db:
            db.execute("INSERT INTO research VALUES (?,?)", (body["id"], canonical(body)))
        return body

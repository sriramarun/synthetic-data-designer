"""Local web UI — a thin HTTP layer over :mod:`sdd.api`.

Deliberately thin, and deliberately local. Every endpoint here is a few lines
that unpack a request, call one `api` function, and return its dict. If an
endpoint needed real logic, the logic would belong in `api` where the CLI can
reach it too.

Three design choices worth stating:

**Runs happen on a worker thread, not in the request.**
    A 500k-row run takes minutes. The endpoint returns a job id immediately and
    the browser polls for progress, so nothing depends on an HTTP connection
    staying open.

**A spec is edited as JSON and validated on every change.**
    The browser holds the whole spec and posts it back to ``/api/check`` as the
    user edits. Validation lives in one place — the same loader the CLI uses —
    so the UI cannot accept a spec the engine would reject.

**Downloads are confined to the job's own directory.**
    Paths are resolved and checked against the workspace root before anything is
    served, because a filename arriving from a browser is untrusted input.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sdd import __version__, api

STATIC = Path(__file__).parent / "static"

# Uploads and outputs live here. One directory keeps cleanup and the download
# path check simple.
WORKSPACE = Path.cwd() / ".sdd-workspace"

# A tape big enough to profile but small enough to keep the UI responsive.
PROFILE_ROW_LIMIT = 200_000

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdd-run")

app = FastAPI(title="Synthetic Data Designer", version=__version__)


def _workspace() -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE


def _safe_path(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` and refuse anything outside ``root``."""
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise HTTPException(400, "path escapes the workspace")
    if not resolved.exists():
        raise HTTPException(404, f"no such file: {candidate}")
    return resolved


# ---------------------------------------------------------------------------
# packs and specs
# ---------------------------------------------------------------------------


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {"version": __version__, "packs": api.list_packs()}


@app.get("/api/packs/{name}")
def get_pack(name: str) -> dict[str, Any]:
    """The full spec of a bundled pack, as JSON the editor can bind to."""
    try:
        spec = api.load(name)
    except api.SddError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "spec": spec.model_dump(mode="json", exclude_none=True, by_alias=True),
        "summary": api.check(name)["spec"],
    }


@app.post("/api/check")
def check(spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Validate a spec. Problems come back as a list, never as a 500."""
    return api.check(spec)


@app.post("/api/spec/yaml")
def spec_to_yaml(spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Render a spec as YAML for the raw-edit escape hatch."""
    import yaml

    return {"yaml": yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)}


@app.post("/api/spec/parse")
def spec_from_yaml(payload: dict[str, str] = Body(...)) -> dict[str, Any]:
    """Parse YAML back into a spec, validating it on the way."""
    import yaml

    try:
        raw = yaml.safe_load(payload.get("yaml", ""))
    except yaml.YAMLError as exc:
        return {"valid": False, "problems": [f"YAML did not parse: {exc}"], "spec": None}
    if not isinstance(raw, dict):
        return {"valid": False, "problems": ["the document is not a mapping"], "spec": None}
    result = api.check(raw)
    result["parsed"] = raw
    return result


# ---------------------------------------------------------------------------
# upload, profile, design
# ---------------------------------------------------------------------------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Store an uploaded tape and report its shape."""
    if not file.filename:
        raise HTTPException(400, "no filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".csv", ".parquet", ".pq"):
        raise HTTPException(400, f"expected .csv or .parquet, got {suffix or 'no extension'}")

    uploads = _workspace() / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    stored = uploads / f"{uuid.uuid4().hex[:8]}{suffix}"
    with stored.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    from sdd.profile import read_sample

    try:
        head = read_sample(stored, max_rows=5)
    except Exception as exc:
        stored.unlink(missing_ok=True)
        raise HTTPException(400, f"could not read the file: {exc}") from exc

    return {
        "file": stored.name,
        "original_name": file.filename,
        "size_bytes": stored.stat().st_size,
        "columns": list(head.columns),
    }


@app.post("/api/design")
def design(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Profile an uploaded tape and build a spec from it."""
    stored = _safe_path(_workspace() / "uploads", payload["file"])
    try:
        result = api.design(
            stored,
            name=payload.get("name") or "profiled",
            id_column=payload.get("id_column") or None,
            time_column=payload.get("time_column") or None,
            state_column=payload.get("state_column") or None,
        )
    except Exception as exc:
        raise HTTPException(400, f"could not build a spec: {exc}") from exc
    return result


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


@app.post("/api/run")
def start_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Queue a run and return its id. Progress is polled, not streamed."""
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise HTTPException(400, "a spec object is required")

    validation = api.check(spec)
    if not validation["valid"]:
        return JSONResponse({"error": "spec is not valid", "problems": validation["problems"]}, 400)

    job_id = uuid.uuid4().hex[:12]
    out_dir = _workspace() / "runs" / job_id
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "waiting for a worker",
            "progress": 0.0,
            "result": None,
            "error": None,
        }

    _pool.submit(
        _execute,
        job_id,
        spec,
        out_dir,
        int(payload.get("num_records") or 10_000),
        int(payload.get("seed") or 42),
        payload.get("periods") or None,
        payload.get("scenario") or None,
    )
    return {"job": job_id}


def _execute(
    job_id: str,
    spec: dict[str, Any],
    out_dir: Path,
    num_records: int,
    seed: int,
    periods: int | None,
    scenario: str | None,
) -> None:
    def progress(stage: str, fraction: float) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["stage"] = stage
                job["progress"] = round(fraction, 4)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"

    try:
        result = api.run(
            spec,
            num_records,
            out_dir,
            seed=seed,
            periods=int(periods) if periods else None,
            scenario=scenario,
            validate_output=True,
            progress=progress,
        )
        # Hand the browser workspace-relative names, never absolute paths.
        root = _workspace().resolve()
        result["files"] = [str(Path(f).resolve().relative_to(root)) for f in result["files"]]
        if result.get("panel"):
            result["panel"] = str(Path(result["panel"]).resolve().relative_to(root))
        with _jobs_lock:
            _jobs[job_id].update(status="done", progress=1.0, stage="done", result=result)
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")


@app.get("/api/run/{job_id}")
def get_run(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return dict(job)


@app.get("/api/download")
def download(path: str) -> FileResponse:
    resolved = _safe_path(_workspace(), path)
    return FileResponse(resolved, filename=resolved.name)


@app.get("/api/preview")
def preview(path: str, rows: int = 25) -> dict[str, Any]:
    """First few rows of an output file, for the results table."""
    from sdd.profile import read_sample

    resolved = _safe_path(_workspace(), path)
    frame = read_sample(resolved, max_rows=max(1, min(rows, 200)))
    return {
        "columns": list(frame.columns),
        "rows": frame.astype(object).where(frame.notna(), None).values.tolist(),
    }


# Mounted last so /api/* wins.
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def serve(host: str = "127.0.0.1", port: int = 8000, *, reload: bool = False) -> None:
    """Run the UI. Binds to localhost only unless told otherwise."""
    import uvicorn

    uvicorn.run("sdd.web.app:app" if reload else app, host=host, port=port, reload=reload)

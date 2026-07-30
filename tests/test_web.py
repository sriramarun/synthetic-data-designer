"""The web layer.

Endpoints are thin, so most of these check the *contract* the browser relies on:
a bad spec comes back as a 200 with readable problems rather than a 500, runs are
asynchronous and pollable, and a filename arriving from a browser cannot reach
outside the workspace.
"""

from __future__ import annotations

import io
import json
import time

import pandas as pd
import pytest

pytest.importorskip("fastapi", reason="the web UI needs the [web] extra")

from fastapi.testclient import TestClient

from sdd import api
from sdd.web import app as web

PACK = "rmbs_nl_green_lion"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose workspace is a throwaway directory."""
    monkeypatch.setattr(web, "WORKSPACE", tmp_path / "workspace")
    return TestClient(web.app)


def wait_for(client, job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/run/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# static and metadata
# ---------------------------------------------------------------------------


def test_the_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Synthetic Data Designer" in response.text


def test_meta_lists_the_packs(client):
    payload = client.get("/api/meta").json()
    assert PACK in payload["packs"]
    assert payload["version"]


def test_a_pack_comes_back_as_editable_json(client):
    payload = client.get(f"/api/packs/{PACK}").json()
    assert payload["spec"]["lifecycle"]["state_column"] == "arrears_bucket"
    assert payload["summary"]["columns"] == 71
    # The editor binds directly to this, so it has to survive a JSON round trip.
    assert json.loads(json.dumps(payload["spec"])) == payload["spec"]


def test_an_unknown_pack_is_a_404(client):
    assert client.get("/api/packs/nope").status_code == 404


# ---------------------------------------------------------------------------
# spec editing
# ---------------------------------------------------------------------------


def test_check_accepts_a_good_spec(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    payload = client.post("/api/check", json=spec).json()
    assert payload["valid"]
    assert payload["problems"] == []


def test_check_returns_readable_problems_not_a_500(client):
    """The UI shows problems[0] as a headline, so it has to be the real message."""
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    spec["lifecycle"]["transitions"][0][0] = 0.85

    response = client.post("/api/check", json=spec)
    assert response.status_code == 200
    payload = response.json()
    assert not payload["valid"]
    assert "sums to 0.857500" in payload["problems"][0]
    # None of pydantic's plumbing should reach the browser.
    joined = " ".join(payload["problems"])
    assert "[type=" not in joined
    assert "validation error for" not in joined
    assert "pydantic.dev" not in joined


def test_yaml_round_trips_through_the_escape_hatch(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    text = client.post("/api/spec/yaml", json=spec).json()["yaml"]
    assert "lifecycle:" in text

    parsed = client.post("/api/spec/parse", json={"yaml": text}).json()
    assert parsed["valid"], parsed["problems"]
    assert parsed["parsed"]["meta"]["name"] == spec["meta"]["name"]


def test_malformed_yaml_is_reported_not_raised(client):
    payload = client.post("/api/spec/parse", json={"yaml": "key: [unclosed"}).json()
    assert not payload["valid"]
    assert "YAML did not parse" in payload["problems"][0]


def test_yaml_that_is_not_a_mapping_is_rejected(client):
    payload = client.post("/api/spec/parse", json={"yaml": "- just\n- a list"}).json()
    assert not payload["valid"]
    assert "not a mapping" in payload["problems"][0]


# ---------------------------------------------------------------------------
# upload and analysis
# ---------------------------------------------------------------------------


def _tape_csv(rows: int = 400) -> bytes:
    frame = pd.concat(
        [
            pd.DataFrame(
                {
                    "loan_id": [f"L{i:05d}" for i in range(rows)],
                    "reporting_date": date,
                    "balance": [100000 + i * 7 for i in range(rows)],
                    "region": ["N", "S"] * (rows // 2),
                    "status": ["Current"] * rows,
                }
            )
            for date in ("2024-01-31", "2024-02-29", "2024-03-31")
        ]
    )
    return frame.to_csv(index=False).encode()


def test_upload_then_design_produces_a_runnable_spec(client):
    upload = client.post(
        "/api/upload", files={"file": ("tape.csv", io.BytesIO(_tape_csv()), "text/csv")}
    )
    assert upload.status_code == 200, upload.text
    stored = upload.json()
    assert "loan_id" in stored["columns"]

    designed = client.post("/api/design", json={"file": stored["file"], "name": "uploaded"}).json()
    assert designed["profile"]["id_column"] == "loan_id"
    assert designed["profile"]["time_column"] == "reporting_date"
    assert client.post("/api/check", json=designed["spec"]).json()["valid"]


def test_an_unsupported_extension_is_refused(client):
    response = client.post(
        "/api/upload", files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    )
    assert response.status_code == 400
    assert ".csv or .parquet" in response.json()["detail"]


def test_an_unreadable_file_is_refused_and_not_kept(client):
    response = client.post(
        "/api/upload",
        files={"file": ("broken.parquet", io.BytesIO(b"not parquet"), "application/octet-stream")},
    )
    assert response.status_code == 400
    uploads = web.WORKSPACE / "uploads"
    assert not uploads.exists() or not list(uploads.iterdir())


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def test_a_run_is_queued_polled_and_completed(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    started = client.post(
        "/api/run", json={"spec": spec, "num_records": 300, "seed": 5, "periods": 4}
    )
    assert started.status_code == 200
    job = wait_for(client, started.json()["job"])

    assert job["status"] == "done", job.get("error")
    result = job["result"]
    assert result["periods"] == 4
    assert len(result["files"]) == 4
    assert result["validation"]["passed"]
    # Paths handed to the browser are workspace-relative, never absolute.
    assert not any(path.startswith("/") for path in result["files"])


def test_a_run_with_an_invalid_spec_is_refused_up_front(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    spec["lifecycle"]["transitions"][0][0] = 0.5
    response = client.post("/api/run", json={"spec": spec, "num_records": 100})
    assert response.status_code == 400
    assert "sums to" in " ".join(response.json()["problems"])


def test_an_unknown_scenario_surfaces_as_a_job_error(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    started = client.post(
        "/api/run",
        json={"spec": spec, "num_records": 100, "periods": 2, "scenario": "apocalypse"},
    )
    job = wait_for(client, started.json()["job"])
    assert job["status"] == "error"
    assert "no scenario" in job["error"]


def test_polling_an_unknown_job_is_a_404(client):
    assert client.get("/api/run/deadbeef").status_code == 404


def test_progress_advances_and_names_a_stage(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    started = client.post("/api/run", json={"spec": spec, "num_records": 200, "periods": 3})
    job = wait_for(client, started.json()["job"])
    assert job["progress"] == 1.0
    assert job["stage"]


# ---------------------------------------------------------------------------
# download and preview
# ---------------------------------------------------------------------------


def test_output_can_be_previewed_and_downloaded(client):
    spec = client.get(f"/api/packs/{PACK}").json()["spec"]
    started = client.post("/api/run", json={"spec": spec, "num_records": 200, "periods": 3})
    result = wait_for(client, started.json()["job"])["result"]

    preview = client.get("/api/preview", params={"path": result["panel"], "rows": 5}).json()
    assert len(preview["columns"]) == 71
    assert len(preview["rows"]) == 5

    download = client.get("/api/download", params={"path": result["files"][0]})
    assert download.status_code == 200
    assert download.text.splitlines()[0].startswith("loan_id,")


@pytest.mark.parametrize(
    "path",
    ["../../../etc/passwd", "/etc/passwd", "runs/../../secrets.txt"],
)
def test_a_path_cannot_escape_the_workspace(client, path):
    """A filename from a browser is untrusted input."""
    response = client.get("/api/download", params={"path": path})
    assert response.status_code in (400, 404)


def test_previewing_a_missing_file_is_a_404(client):
    assert client.get("/api/preview", params={"path": "runs/nope/panel.parquet"}).status_code == 404


# ---------------------------------------------------------------------------
# the error cleaner, directly
# ---------------------------------------------------------------------------


def test_explain_problems_keeps_the_message_and_drops_the_plumbing():
    raw = (
        "invalid design spec:\n"
        "1 validation error for DesignSpec\n"
        "lifecycle\n"
        "  Value error, transition matrix row 0 sums to 0.85, expected 1.0 "
        "[type=value_error, input_value={'a': [1, 2]}, input_type=dict]\n"
        "    For further information visit https://errors.pydantic.dev/2.12/v/value_error"
    )
    problems = api.explain_problems(ValueError(raw))
    assert problems == ["lifecycle: transition matrix row 0 sums to 0.85, expected 1.0"]


def test_explain_problems_passes_our_own_bullets_through():
    raw = "spec 'x' has 2 problem(s):\n  - first thing is wrong\n  - second thing is wrong"
    assert api.explain_problems(ValueError(raw)) == [
        "first thing is wrong",
        "second thing is wrong",
    ]


def test_explain_problems_never_returns_nothing():
    assert api.explain_problems(ValueError("")) == ["the spec could not be validated"]

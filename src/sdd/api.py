"""The single entry point everything else calls.

Every function here takes plain values and returns JSON-serialisable dicts. That
is deliberate: the CLI is its first consumer today and a web API will be its
second, and neither should reach past this module into the engine. Keeping one
contract means the UI phase is a thin layer rather than a second implementation.

Three things every run produces, beyond the data itself:

**A manifest**
    Spec hash, seed, row counts, library versions, timings. Written next to the
    output, so a dataset found six months later can be traced back to what made
    it and regenerated exactly.

**Progress callbacks**
    ``progress(stage, fraction)``. A 500k-row run takes minutes; a UI needs to
    show something during them.

**Errors as values, not tracebacks**
    A bad spec is a normal outcome of a user editing a file. It comes back as a
    structured problem list, not an exception a web handler has to catch.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from sdd import __version__

ProgressFn = Callable[[str, float], None]

MANIFEST_NAME = "run_manifest.json"


class SddError(RuntimeError):
    """Something the caller asked for cannot be done."""


# ---------------------------------------------------------------------------
# spec handling
# ---------------------------------------------------------------------------


def load(spec: str | Path | dict[str, Any]) -> Any:
    """Accept a spec as a path, a pack name, or an already-parsed mapping."""
    from sdd.spec import load_spec, load_spec_dict

    if isinstance(spec, dict):
        return load_spec_dict(spec)
    path = Path(spec)
    if not path.exists():
        packed = pack_path(str(spec))
        if packed is None:
            raise SddError(
                f"no spec found at {spec!r}, and no bundled pack of that name "
                f"(available: {', '.join(list_packs()) or 'none'})"
            )
        path = packed
    return load_spec(path)


def packs_dir() -> Path:
    """Where the bundled packs live, whether installed or run from a checkout."""
    installed = Path(__file__).parent / "packs"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[2] / "packs"


def list_packs() -> list[str]:
    directory = packs_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def pack_path(name: str) -> Path | None:
    candidate = packs_dir() / f"{Path(name).stem}.yaml"
    return candidate if candidate.exists() else None


def spec_hash(spec: Any) -> str:
    """A stable fingerprint of a spec's content, for the manifest."""
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# Everything from `[type=` onward is pydantic's machine detail, and it embeds
# the whole rejected value — which itself contains brackets, so this cannot stop
# at the first closing one.
_PYDANTIC_TAIL = re.compile(r"\s*\[type=.*$")
_NOISE = re.compile(
    r"^(?:invalid design spec:|.* is not a valid design spec:|"
    r"\d+ validation error.*|spec '.*' has \d+ problem\(s\):)$"
)


def explain_problems(error: Exception) -> list[str]:
    """Turn a validation failure into lines a person can act on.

    The messages written into :mod:`sdd.spec.schema` are deliberately specific —
    *"transition matrix row 0 ('Performing') sums to 0.857500, expected 1.0"*.
    Pydantic then wraps each one in a header, a field path, a ``[type=…]`` tail
    carrying the entire rejected input, and a docs link. Handed to a UI verbatim
    the useful sentence ends up as the fourth of five lines, behind
    *"invalid design spec:"*, so the interface shows the user nothing.

    This keeps the sentence and the field it belongs to, and drops the rest.
    """
    problems: list[str] = []
    field: str | None = None

    for raw in str(error).splitlines():
        line = raw.rstrip()
        if not line.strip() or _NOISE.match(line.strip()):
            continue
        if line.strip().startswith("For further information visit"):
            continue

        stripped = line.strip()
        # Our own check_spec bullets are already clean.
        if stripped.startswith("- "):
            problems.append(stripped[2:])
            continue
        # An unindented line with no message is pydantic naming the field.
        if not line.startswith(" ") and "error," not in stripped:
            field = stripped
            continue

        message = _PYDANTIC_TAIL.sub("", stripped)
        for prefix in ("Value error, ", "Assertion failed, "):
            if message.startswith(prefix):
                message = message[len(prefix) :]
        problems.append(f"{field}: {message}" if field else message)

    return problems or [str(error).strip() or "the spec could not be validated"]


def check(spec: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Validate a spec and report problems as data rather than an exception."""
    from sdd.spec import SpecError

    try:
        loaded = load(spec)
    except (SpecError, SddError) as exc:
        return {"valid": False, "problems": explain_problems(exc), "spec": None}
    return {
        "valid": True,
        "problems": [],
        "spec": {
            "name": loaded.meta.name,
            "asset_class": loaded.meta.asset_class,
            "hash": spec_hash(loaded),
            "columns": len(loaded.output_columns()),
            "periods": loaded.entity.calendar.periods,
            "has_lifecycle": loaded.lifecycle is not None,
            "scenarios": sorted(loaded.scenarios),
            "needs_review": [c.name for c in loaded.columns if c.review],
        },
    }


# ---------------------------------------------------------------------------
# profiling
# ---------------------------------------------------------------------------


def profile(
    sample: str | Path | pd.DataFrame,
    *,
    id_column: str | None = None,
    time_column: str | None = None,
    state_column: str | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Analyse a sample tape. Returns the profile as a plain dict."""
    from sdd.profile import profile_dataset

    result = profile_dataset(
        sample,
        id_column=id_column,
        time_column=time_column,
        state_column=state_column,
        max_rows=max_rows,
    )
    return result.to_dict()


def design(
    sample: str | Path | pd.DataFrame,
    *,
    structure: str | Path | None = None,
    name: str = "profiled",
    out: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Profile a sample and write a runnable spec — the whole analysis step."""
    from sdd.profile import build_spec
    from sdd.spec import dump_spec

    spec, prof = build_spec(
        sample, structure=str(structure) if structure else None, name=name, **kwargs
    )

    written = None
    if out:
        written = str(
            dump_spec(
                spec,
                out,
                header=(
                    f"# Generated by sdd {__version__} from a sample of {prof.rows:,} rows.\n"
                    "# Every generator below is an inference. Columns carrying a `review:`\n"
                    "# note were inferred with low confidence — check those first.\n"
                ),
            )
        )

    return {
        "spec": spec.model_dump(mode="json", exclude_none=True, by_alias=True),
        "spec_path": written,
        "profile": prof.to_dict(),
        "needs_review": [c.name for c in spec.columns if c.review],
        "summary": prof.summary(),
    }


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


def generate(
    spec: str | Path | dict[str, Any],
    num_records: int,
    *,
    out: str | Path | None = None,
    seed: int = 42,
    backend: str = "numpy",
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Build the period-0 book only."""
    from sdd.generate import build_book

    loaded = load(spec)
    started = time.time()
    book = build_book(loaded, num_records, seed=seed, backend=backend, progress=progress)

    written = None
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        book[loaded.output_columns()].to_parquet(path, index=False)
        written = str(path)

    return {
        "rows": len(book),
        "columns": len(loaded.output_columns()),
        "path": written,
        "seconds": round(time.time() - started, 2),
        "spec_hash": spec_hash(loaded),
    }


def run(
    spec: str | Path | dict[str, Any],
    num_records: int,
    out_dir: str | Path,
    *,
    seed: int = 42,
    periods: int | None = None,
    scenario: str | None = None,
    backend: str = "numpy",
    validate_output: bool = True,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Generate and age in one call — the normal path.

    ``scenario`` selects a named stress overlay from the spec. ``periods``
    overrides the calendar, which is how a UI offers "how many months?" without
    editing the spec.
    """
    from sdd.age.panel import run_ageing
    from sdd.generate import build_book

    loaded = load(spec)
    # The hash of the spec *as loaded*, before any run-time override. Recording
    # both this and the effective hash is what lets someone holding a dataset
    # six months later say "this came from pack X, run with these changes" —
    # the effective hash alone matches nothing on disk.
    base_hash = spec_hash(loaded)

    if periods:
        loaded = loaded.model_copy(deep=True)
        loaded.entity.calendar.periods = periods

    chosen = None
    if scenario:
        if scenario not in loaded.scenarios:
            raise SddError(
                f"spec {loaded.meta.name!r} has no scenario {scenario!r} "
                f"(available: {', '.join(sorted(loaded.scenarios)) or 'none'})"
            )
        chosen = loaded.scenarios[scenario]
        loaded = _apply_scenario(loaded, chosen)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    started = time.time()

    def book_progress(stage: str, fraction: float) -> None:
        if progress:
            progress(f"book: {stage}", fraction * 0.35)

    book = build_book(loaded, num_records, seed=seed, backend=backend, progress=book_progress)
    book_seconds = time.time() - started

    def age_progress(stage: str, fraction: float) -> None:
        if progress:
            progress(f"ageing: {stage}", 0.35 + fraction * 0.6)

    aged_at = time.time()
    result = run_ageing(loaded, book, out_path, seed=seed, scenario=chosen, progress=age_progress)
    age_seconds = time.time() - aged_at

    report = None
    if validate_output and result.get("panel"):
        from sdd.validate import validate_panel

        report = validate_panel(loaded, result["panel"]).to_dict()

    if progress:
        progress("done", 1.0)

    payload = {
        "spec": loaded.meta.name,
        "spec_hash": spec_hash(loaded),
        "base_spec_hash": base_hash,
        "scenario": scenario,
        "seed": seed,
        "entities": num_records,
        "periods": result["periods"],
        "surviving_entities": result["final_rows"],
        "files": result["files"],
        "panel": result["panel"],
        "mix": result["mix"],
        "validation": report,
        "timings": {
            "book_seconds": round(book_seconds, 2),
            "ageing_seconds": round(age_seconds, 2),
            "total_seconds": round(time.time() - started, 2),
        },
    }
    _write_manifest(out_path, loaded, payload)
    return payload


def _apply_scenario(spec: Any, scenario: Any) -> Any:
    """Return a copy of the spec with the scenario's overlay baked in.

    Transition stressing happens here rather than inside the ageing loop so the
    stressed matrix is validated by the same rules as a hand-written one — a
    scenario cannot smuggle in a row that does not sum to 1.
    """
    from sdd.age.panel import stress_transitions
    from sdd.spec import load_spec_dict

    stressed = spec.model_copy(deep=True)
    if stressed.lifecycle:
        matrix = stress_transitions(spec, scenario)
        if matrix:
            stressed.lifecycle.transitions = matrix
    for column in scenario.rate_columns:
        stressed.derivations.append(_rate_shift_derivation(column, scenario.rate_shift))
    return load_spec_dict(stressed.model_dump(mode="json", by_alias=True))


def _rate_shift_derivation(column: str, shift: float) -> Any:
    from sdd.spec.schema import Derivation

    return Derivation(target=column, expr=f"{column} + {shift}", stage="book")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate(spec: str | Path | dict[str, Any], panel: str | Path | pd.DataFrame) -> dict[str, Any]:
    """Run every spec-derived invariant against a panel."""
    from sdd.validate import validate_panel

    loaded = load(spec)
    report = validate_panel(loaded, panel)
    return {**report.to_dict(), "summary": report.summary()}


def fidelity(
    reference: str | Path | pd.DataFrame,
    synthetic: str | Path | pd.DataFrame,
    *,
    spec: str | Path | dict[str, Any] | None = None,
    id_column: str | None = None,
    time_column: str | None = None,
    state_column: str | None = None,
) -> dict[str, Any]:
    """Score synthetic data against the sample it was meant to resemble."""
    from sdd.profile import read_sample
    from sdd.validate import compare

    if spec is not None:
        loaded = load(spec)
        id_column = id_column or loaded.entity.id_column
        time_column = time_column or loaded.entity.time_column
        state_column = state_column or (loaded.lifecycle.state_column if loaded.lifecycle else None)

    left = reference if isinstance(reference, pd.DataFrame) else read_sample(reference)
    right = synthetic if isinstance(synthetic, pd.DataFrame) else read_sample(synthetic)

    report = compare(
        left,
        right,
        id_column=id_column,
        time_column=time_column,
        state_column=state_column,
    )
    return {**report.to_dict(), "summary": report.summary()}


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _write_manifest(out_dir: Path, spec: Any, payload: dict[str, Any]) -> Path:
    """Record everything needed to reproduce this run exactly."""
    manifest = {
        "generated_by": f"sdd {__version__}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "spec": {
            "name": spec.meta.name,
            "asset_class": spec.meta.asset_class,
            "hash": payload["spec_hash"],
            "base_hash": payload["base_spec_hash"],
            "version": spec.spec_version,
        },
        "inputs": {
            "entities": payload["entities"],
            "periods": payload["periods"],
            "seed": payload["seed"],
            "scenario": payload["scenario"],
        },
        "outputs": {
            "files": len(payload["files"]),
            "panel": payload["panel"],
            "surviving_entities": payload["surviving_entities"],
        },
        "timings": payload["timings"],
        "validation_passed": (payload["validation"] or {}).get("passed"),
        "library_versions": _versions(),
    }
    path = out_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def _versions() -> dict[str, str]:
    import numpy
    import scipy

    return {
        "numpy": numpy.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
    }

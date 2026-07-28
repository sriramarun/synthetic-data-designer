"""Build the period-0 book: one row per entity, at the first cut-off.

This is the "origination snapshot" — every loan as it looks on day one of the
panel. :mod:`sdd.age.panel` then walks it forward.

Order of operations, and why:

1. **Sample** each column in declaration order, so a conditional generator always
   sees its parent.
2. **Apply constants** — deal-level facts that never vary.
3. **Assign ids** from ``entity.id_format`` when given. Upstream did this because
   an 8-hex-character random id collides about 29 times in 500k draws; a
   sequential id cannot collide at all.
4. **Run derivations** in order — arithmetic that pandas does far faster than a
   per-row sampler would.
5. **Coerce dtypes**, drop helpers, order columns.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from sdd.calendar import period_dates
from sdd.generate.deriver import evaluate_on
from sdd.generate.samplers import sample
from sdd.spec.schema import Bucket, Derivation, DesignSpec, DType

ProgressFn = Callable[[str, float], None]


class GenerationError(RuntimeError):
    """Generation failed in a way the spec should have prevented."""


def build_book(
    spec: DesignSpec,
    num_records: int,
    *,
    seed: int = 42,
    backend: str = "numpy",
    progress: ProgressFn | None = None,
) -> pd.DataFrame:
    """Generate the period-0 book with ``num_records`` entities."""
    if num_records < 1:
        raise GenerationError(f"num_records must be at least 1, got {num_records}")

    def report(stage: str, frac: float) -> None:
        if progress:
            progress(stage, frac)

    rng = np.random.default_rng(seed)

    if backend == "nemo":
        from sdd.generate.backend_nemo import sample_columns_nemo

        df = sample_columns_nemo(spec, num_records, seed=seed)
    else:
        df = _sample_columns(spec, num_records, rng, report)

    report("constants", 0.55)
    df = _apply_constants(spec, df)

    report("identifiers", 0.6)
    df = _apply_id_format(spec, df)

    report("time column", 0.62)
    dates = period_dates(spec.entity.calendar)
    df[spec.entity.time_column] = dates[0].strftime("%Y-%m-%d")

    # Apply the lifecycle's per-state values before derivations so period 0 and
    # every later period agree on what a state implies. Without this the book
    # would need its own copy of the state -> days-past-due mapping, and the two
    # would drift apart the first time someone edited one of them.
    report("state fields", 0.63)
    df = _apply_initial_state_fields(spec, df)

    report("derivations", 0.65)
    df = apply_derivations(spec, df, stage="book")

    report("finalise", 0.95)
    df = finalise(spec, df)
    report("done", 1.0)
    return df


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _sample_columns(
    spec: DesignSpec, n: int, rng: np.random.Generator, report: ProgressFn
) -> pd.DataFrame:
    df = pd.DataFrame(index=pd.RangeIndex(n))
    sampled = [c for c in spec.columns if c.generator is not None]
    for i, col in enumerate(sampled):
        try:
            df[col.name] = sample(col.generator, n, rng, df)
        except Exception as exc:
            raise GenerationError(f"column {col.name!r} failed to sample: {exc}") from exc
        if sampled:
            report(f"sampling {col.name}", 0.5 * (i + 1) / len(sampled))
    return df


def _apply_constants(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    for name, value in spec.constants.items():
        df[name] = value
    return df


def _apply_id_format(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    fmt = spec.entity.id_format
    if not fmt:
        return df
    context: dict[str, Any] = {**spec.params, **spec.constants}
    try:
        df[spec.entity.id_column] = [fmt.format(seq=i, **context) for i in range(1, len(df) + 1)]
    except KeyError as exc:
        raise GenerationError(
            f"entity.id_format {fmt!r} uses placeholder {exc} which is not in "
            f"`params` or `constants` (available: {sorted(context)})"
        ) from exc
    return df


def _apply_initial_state_fields(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    lc = spec.lifecycle
    if lc is None or not lc.state_fields:
        return df
    if lc.state_column not in df.columns:
        raise GenerationError(
            f"lifecycle.state_column {lc.state_column!r} is not produced at period 0; "
            "give it a generator so every entity starts in a known state"
        )
    from sdd.age.panel import apply_state_fields

    return apply_state_fields(spec, df, df[lc.state_column].to_numpy())


def apply_derivations(
    spec: DesignSpec, df: pd.DataFrame, *, stage: str, extra: dict[str, Any] | None = None
) -> pd.DataFrame:
    """Run the derivations whose ``stage`` matches, in declaration order."""
    env: dict[str, Any] = {**spec.params, **(extra or {})}
    for d in spec.derivations:
        if d.stage != stage and d.stage != "both":
            continue
        try:
            values = _derive_one(spec, d, df, env)
        except Exception as exc:
            raise GenerationError(f"derivation {d.target!r} failed: {exc}") from exc
        if d.round is not None and not isinstance(values, pd.Categorical):
            values = np.round(np.asarray(values, dtype=float), d.round)
        df[d.target] = values
        if d.dtype:
            df[d.target] = _coerce(df[d.target], d.dtype)
    return df


def _derive_one(spec: DesignSpec, d: Derivation, df: pd.DataFrame, env: dict[str, Any]) -> Any:
    if d.kind == "expr":
        assert d.expr
        return evaluate_on(d.expr, df, env)

    if d.kind == "when":
        assert d.rules
        n = len(df)
        out = np.full(n, d.else_, dtype=object)
        # Later rules must not overwrite earlier ones: first match wins.
        assigned = np.zeros(n, dtype=bool)
        for rule in d.rules:
            cond = evaluate_on(rule.if_, df, env)
            mask = np.asarray(cond, dtype=bool) if np.ndim(cond) else np.full(n, bool(cond))
            take = mask & ~assigned
            out[take] = rule.then
            assigned |= take
        return out

    if d.kind == "bucket":
        assert d.bucket and d.source
        return apply_bucket(spec.buckets[d.bucket], df[d.source])

    if d.kind == "format":
        assert d.template
        parts = {name: np.asarray(evaluate_on(e, df, env)) for name, e in d.args.items()}
        n = len(df)
        broadcast = {name: (np.full(n, v) if v.ndim == 0 else v) for name, v in parts.items()}
        return [d.template.format(**{k: v[i] for k, v in broadcast.items()}) for i in range(n)]

    raise GenerationError(f"unknown derivation kind {d.kind!r}")


def apply_bucket(bucket: Bucket, values: pd.Series) -> pd.Series:
    """Bin a numeric column into labelled bands."""
    return pd.cut(
        values,
        bins=bucket.bins,
        labels=bucket.labels,
        right=bucket.right,
        include_lowest=bucket.include_lowest,
    ).astype(str)


# ---------------------------------------------------------------------------
# dtypes and column order
# ---------------------------------------------------------------------------

_PANDAS_DTYPE: dict[DType, str] = {
    "int": "int64",
    "float": "float64",
    "str": "object",
    "bool": "bool",
    "category": "object",
}


def _coerce(series: pd.Series, dtype: DType) -> pd.Series:
    if dtype == "date":
        return pd.to_datetime(series).dt.strftime("%Y-%m-%d")
    if dtype == "int":
        # Round first: float -> int truncates, which quietly biases counters low.
        return np.round(pd.to_numeric(series, errors="coerce")).astype("int64")
    if dtype == "float":
        return pd.to_numeric(series, errors="coerce").astype("float64")
    return series.astype(_PANDAS_DTYPE[dtype])


def coerce_dtypes(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    for col in spec.columns:
        if col.dtype and col.name in df.columns:
            df[col.name] = _coerce(df[col.name], col.dtype)
    for ctr in spec.dynamics.counters:
        if ctr.dtype and ctr.column in df.columns:
            df[ctr.column] = _coerce(df[ctr.column], ctr.dtype)
    return df


def finalise(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    """Coerce dtypes and put the columns in output order.

    Helper columns are *retained* here — the ageing engine may still need them —
    and dropped only when writing to disk (see :func:`sdd.age.panel.to_output`).
    """
    df = coerce_dtypes(spec, df)
    ordered = spec.output_columns()
    missing = [c for c in ordered if c not in df.columns]
    if missing:
        raise GenerationError(
            f"spec declares output columns that generation never produced: {missing}"
        )
    tail = [c for c in df.columns if c not in ordered]
    return df[ordered + tail]

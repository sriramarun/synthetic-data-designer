"""The ageing loop: turn a period-0 book into a panel.

One pass per cut-off. The order below is not arbitrary — each step depends on the
one before it:

1. **Lifecycle step.** Who prepaid, who fell behind, who charged off. Everything
   downstream keys off the new state.
2. **Counters.** Seasoning up, remaining term down.
3. **Amortisation.** Only the entities the lifecycle says are paying.
4. **Indices.** House prices, residual values, whatever the spec overlays.
5. **Accruals.** Arrears grow for another missed payment.
6. **State overrides.** ``state_fields`` is applied last among the value steps so
   a forced value always wins — a redeemed loan's balance is 0 regardless of what
   the amortisation kernel computed.
7. **Per-period derivations.** Ratios and buckets, computed from final values so
   an LTV always matches the balance printed beside it.
8. **Emit**, then drop anything terminal before the next period.

A terminal row is written *once* — the period it goes terminal shows the final
zero balance — and then leaves the pool. That is what makes this a closed pool
with attrition rather than a fixed panel.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from sdd.age.dynamics import (
    amortise,
    apply_accruals,
    apply_counters,
    apply_indices,
    seed_accrual_counters,
)
from sdd.age.lifecycle import LifecycleEngine
from sdd.calendar import format_filename, period_dates
from sdd.generate.book import apply_derivations, coerce_dtypes
from sdd.spec.schema import DesignSpec, Scenario

ProgressFn = Callable[[str, float], None]


class AgeingError(RuntimeError):
    """The panel could not be advanced."""


def run_ageing(
    spec: DesignSpec,
    book: pd.DataFrame,
    out_dir: str | Path,
    *,
    seed: int = 42,
    scenario: Scenario | None = None,
    progress: ProgressFn | None = None,
    write_files: bool = True,
) -> dict:
    """Age ``book`` across the spec's calendar, writing one file per period.

    Returns a summary dict: per-period row counts, the state mix, and the paths
    written — enough for a caller (CLI today, API later) to report without
    re-reading the output.
    """
    if spec.lifecycle is None:
        raise AgeingError(
            "this spec has no `lifecycle` section, so there is nothing to age. "
            "Generate the period-0 book instead."
        )

    out_path = Path(out_dir)
    if write_files:
        out_path.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    engine = LifecycleEngine(spec.lifecycle, spec.entity.calendar.periods_per_year)
    dates = period_dates(spec.entity.calendar)
    lc = spec.lifecycle

    performing_state = lc.states[0]
    hazard_mult, index_shift = _scenario_knobs(spec, scenario)

    current = book.copy().reset_index(drop=True)
    dwell = engine.initial_dwell(len(current), engine.to_idx(current[lc.state_column].to_numpy()))
    accrual_counters = seed_accrual_counters(current, spec.dynamics.accruals, _dpd_column(spec))

    written: list[str] = []
    mixes: list[dict] = []
    frames: list[pd.DataFrame] = []

    for period, date in enumerate(dates):
        if period > 0:
            if current.empty:
                break
            current, dwell, accrual_counters = _step(
                spec,
                engine,
                current,
                dwell,
                accrual_counters,
                period=period,
                date=date,
                rng=rng,
                hazard_mult=hazard_mult,
                index_shift=index_shift,
                performing_state=performing_state,
            )

        out = to_output(spec, current)
        mix = current[lc.state_column].value_counts().to_dict()
        mixes.append({"period": period, "date": date.strftime("%Y-%m-%d"), "rows": len(out), **mix})

        if write_files:
            written += _write_period(spec, out, date, period, out_path)
        if spec.emit.write_panel:
            frames.append(out)

        if progress:
            progress(f"period {period + 1}/{len(dates)}", (period + 1) / len(dates))

        # Terminal rows were just written; they do not survive into the next period.
        terminal = engine.is_terminal(engine.to_idx(current[lc.state_column].to_numpy()))
        if terminal.any():
            keep = ~terminal
            current = current.loc[keep].reset_index(drop=True)
            dwell = {k: v[keep] for k, v in dwell.items()}
            accrual_counters = {k: v[keep] for k, v in accrual_counters.items()}

    panel_path = None
    if spec.emit.write_panel and frames and write_files:
        panel = pd.concat(frames, ignore_index=True)
        panel_path = out_path / spec.emit.panel_filename
        panel.to_parquet(panel_path, index=False)

    return {
        "periods": len(mixes),
        "files": written,
        "panel": str(panel_path) if panel_path else None,
        "mix": mixes,
        "final_rows": len(current),
    }


# ---------------------------------------------------------------------------
# one period
# ---------------------------------------------------------------------------


def _step(
    spec: DesignSpec,
    engine: LifecycleEngine,
    df: pd.DataFrame,
    dwell: dict[str, np.ndarray],
    accrual_counters: dict[str, np.ndarray],
    *,
    period: int,
    date: pd.Timestamp,
    rng: np.random.Generator,
    hazard_mult: dict[str, float],
    index_shift: dict[str, float],
    performing_state: str,
) -> tuple[pd.DataFrame, dict[str, np.ndarray], dict[str, np.ndarray]]:
    lc = spec.lifecycle
    assert lc is not None
    df = df.reset_index(drop=True)

    # 1. lifecycle
    prev_idx = engine.to_idx(df[lc.state_column].to_numpy())
    new_idx, dwell = engine.step(prev_idx, dwell, rng, hazard_multipliers=hazard_mult)
    labels = engine.to_label(new_idx)
    df[lc.state_column] = labels

    terminal_mask = engine.is_terminal(new_idx)
    active_mask = ~terminal_mask

    # 2. counters
    if spec.dynamics.counters:
        df = apply_counters(df, spec.dynamics.counters, spec.params)

    # 3. amortisation
    am = spec.dynamics.amortisation
    if am:
        if am.only_when_state is None:
            pays = active_mask
        else:
            wanted = (
                [am.only_when_state] if isinstance(am.only_when_state, str) else am.only_when_state
            )
            pays = np.isin(labels, wanted)
        df[am.balance] = np.round(amortise(df, am, pays_mask=pays, active_mask=active_mask), 2)

    # 4. indices
    if spec.dynamics.indices:
        df = apply_indices(
            df,
            spec.dynamics.indices,
            period,
            spec.entity.calendar.periods_per_year,
            rng,
            annual_shift=index_shift,
        )

    # 5. accruals
    if spec.dynamics.accruals:
        df, accrual_counters = apply_accruals(
            df,
            spec.dynamics.accruals,
            state_labels=labels,
            terminal_mask=terminal_mask,
            performing_state=performing_state,
            counters=accrual_counters,
        )

    # 6. forced per-state values — these win over everything computed above
    df = apply_state_fields(spec, df, labels)

    # 7. time column, then per-period derivations that depend on it
    df[spec.entity.time_column] = date.strftime("%Y-%m-%d")
    df = apply_derivations(spec, df, stage="period", extra={**spec.params, "period": period})
    df = coerce_dtypes(spec, df)

    return df, dwell, accrual_counters


def apply_state_fields(spec: DesignSpec, df: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """Force the values a spec pins to each state (zero balance when redeemed, …)."""
    lc = spec.lifecycle
    if lc is None or not lc.state_fields:
        return df
    for state, fields in lc.state_fields.items():
        mask = labels == state
        if not mask.any():
            continue
        for column, value in fields.items():
            if column not in df.columns:
                df[column] = value
            else:
                df.loc[mask, column] = value
    return df


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def to_output(spec: DesignSpec, df: pd.DataFrame) -> pd.DataFrame:
    """Select and order the columns that go to disk, dropping helpers."""
    cols = spec.output_columns()
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise AgeingError(f"output columns missing from the panel: {missing}")
    return df[cols].copy()


def _write_period(
    spec: DesignSpec, out: pd.DataFrame, date: pd.Timestamp, period: int, out_dir: Path
) -> list[str]:
    written: list[str] = []
    stem = format_filename(spec.emit.filename, date, period, spec.meta.name)
    base, ext = os.path.splitext(stem)
    for fmt in spec.emit.formats:
        path = out_dir / (stem if ext.lstrip(".") == fmt else f"{base}.{fmt}")
        if fmt == "csv":
            out.to_csv(path, index=False, float_format=spec.emit.float_format)
        else:
            out.to_parquet(path, index=False)
        written.append(str(path))
    return written


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


def _scenario_knobs(
    spec: DesignSpec, scenario: Scenario | None
) -> tuple[dict[str, float], dict[str, float]]:
    if scenario is None:
        return {}, {}
    hazard_mult = {
        hz.name: scenario.prepayment_multiplier
        for hz in (spec.lifecycle.hazards if spec.lifecycle else [])
        if hz.kind == "bernoulli"
    }
    return hazard_mult, dict(scenario.index_shift)


def stress_transitions(spec: DesignSpec, scenario: Scenario) -> list[list[float]] | None:
    """Scale the probability of *worsening* and renormalise each row.

    "Worsening" means moving to a state later in the declared order — the spec is
    expected to list states best-first, which every delinquency ladder already
    does. Probability taken from the worsening cells is removed proportionally
    from the rest of the row, so each row still sums to 1.
    """
    lc = spec.lifecycle
    if lc is None or lc.transitions is None or scenario.default_multiplier == 1.0:
        return None if lc is None else lc.transitions

    matrix = np.asarray(lc.transitions, dtype=float).copy()
    n = matrix.shape[0]
    for i in range(n):
        worse = np.zeros(n, dtype=bool)
        worse[i + 1 :] = True
        before = matrix[i, worse].sum()
        if before <= 0:
            continue
        after = float(np.clip(before * scenario.default_multiplier, 0.0, 0.999999))
        matrix[i, worse] *= after / before
        rest = ~worse
        rest_before = matrix[i, rest].sum()
        if rest_before > 0:
            matrix[i, rest] *= (1.0 - after) / rest_before
        matrix[i] /= matrix[i].sum()
    return matrix.tolist()


def _dpd_column(spec: DesignSpec) -> str | None:
    """Best guess at a days-past-due column, used to seed arrears counters."""
    for candidate in ("days_past_due", "dpd", "days_in_arrears"):
        if spec.column(candidate) or candidate in {d.target for d in spec.derivations}:
            return candidate
    return None

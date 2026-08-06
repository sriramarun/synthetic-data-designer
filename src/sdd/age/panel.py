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

    opening_size = len(current)
    # Identifiers continue from the opening book, so a later cohort cannot be
    # handed an identifier an existing entity already holds.
    next_index = opening_size
    originated = 0

    for period, date in enumerate(dates):
        joined = 0
        if period > 0:
            if current.empty and spec.originations is None:
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
            if spec.originations is not None:
                current, dwell, accrual_counters, joined = originate(
                    spec,
                    engine,
                    current,
                    dwell,
                    accrual_counters,
                    period=period,
                    date=date,
                    rng=rng,
                    opening_size=opening_size,
                    next_index=next_index,
                )
                next_index += joined
                originated += joined

        out = to_output(spec, current)
        mix = current[lc.state_column].value_counts().to_dict()
        mixes.append(
            {
                "period": period,
                "date": date.strftime("%Y-%m-%d"),
                "rows": len(out),
                "originated": joined,
                **mix,
            }
        )

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
        "opening_entities": opening_size,
        "originated": originated,
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

    # 4b. recovery — booked on the way into a write-off state, from the balance
    # the entity carried in. Computed before `state_fields`, which is usually
    # what zeroes that balance.
    if spec.dynamics.recovery:
        df = apply_recovery(spec, df, previous=engine.to_label(prev_idx), current=labels)

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


def originate(
    spec: DesignSpec,
    engine: LifecycleEngine,
    df: pd.DataFrame,
    dwell: dict[str, np.ndarray],
    accrual_counters: dict[str, np.ndarray],
    *,
    period: int,
    date: pd.Timestamp,
    rng: np.random.Generator,
    opening_size: int,
    next_index: int,
) -> tuple[pd.DataFrame, dict[str, np.ndarray], dict[str, np.ndarray], int]:
    """Add the entities joining the pool at this cut-off.

    They are built by the same book builder as the opening cohort, so they are
    drawn from the same distributions and carry the same derived columns — a new
    loan looks like the portfolio it is joining. Three things differ:

    *They enter at this cut-off, not the first.* The time column is stamped with
    this period's date rather than the calendar's start.

    *They have not been aged.* This runs after the whole ageing step, so a loan
    written this month is not also charged a month of interest this month.

    *Under ``fresh`` they are new, not acquired.* The healthiest state, and every
    upward-ticking counter set to zero — a counter that rises each period
    measures elapsed time, and none has elapsed for a loan written today.
    """
    plan = spec.originations
    assert plan is not None
    count = plan.count_for(period, opening_size)
    if count < 1:
        return df, dwell, accrual_counters, 0

    from sdd.generate.book import build_book

    # Drawn from the run's own generator so the whole panel reproduces from one
    # seed, and differs period to period.
    cohort_seed = int(rng.integers(0, 2**32 - 1))
    new = build_book(
        spec,
        count,
        seed=cohort_seed,
        id_offset=next_index,
        at=date.strftime("%Y-%m-%d"),
    )

    lc = spec.lifecycle
    assert lc is not None
    if plan.fresh:
        new[lc.state_column] = lc.states[0]
        for counter in spec.dynamics.counters:
            if counter.step is not None and counter.step > 0 and counter.column in new.columns:
                new[counter.column] = 0
    for column, value in plan.reset.items():
        new[column] = value
    if plan.reset_expr:
        from sdd.generate.deriver import evaluate_on

        env = {
            **spec.params,
            "period": period,
            "period_year": date.year,
            "period_month": date.month,
            "period_day": date.day,
        }
        for column, expression in plan.reset_expr.items():
            try:
                new[column] = evaluate_on(expression, new, env)
            except Exception as exc:
                raise AgeingError(f"originations.reset_expr for {column!r} failed: {exc}") from exc

    labels = new[lc.state_column].to_numpy()
    new = apply_state_fields(spec, new, labels)
    # Re-derived after the resets, so a ratio computed at origination matches the
    # values the entity actually enters with.
    new = apply_derivations(spec, new, stage="book")
    new = coerce_dtypes(spec, new)

    missing = [c for c in df.columns if c not in new.columns]
    if missing:
        raise AgeingError(
            f"the entities joining at period {period} are missing columns the pool already "
            f"has: {missing}"
        )
    new = new[list(df.columns)]

    id_column = spec.entity.id_column
    clashing = set(new[id_column]) & set(df[id_column])
    if clashing:
        raise AgeingError(
            f"{len(clashing)} entity identifier(s) created at period {period} already exist in "
            f"the pool, e.g. {sorted(clashing)[:3]}. An open pool needs identifiers that cannot "
            f"repeat: give `entity.id_format` a '{{seq}}' placeholder, or generate "
            f"{id_column!r} with a `sequence` or `uuid` generator"
        )

    joined_dwell = engine.initial_dwell(len(new), engine.to_idx(labels))
    dwell = {
        name: np.concatenate([values, joined_dwell.get(name, np.zeros(len(new), dtype=np.int32))])
        for name, values in dwell.items()
    }
    joined_counters = seed_accrual_counters(new, spec.dynamics.accruals, _dpd_column(spec))
    accrual_counters = {
        name: np.concatenate(
            [values, joined_counters.get(name, np.zeros(len(new), dtype=values.dtype))]
        )
        for name, values in accrual_counters.items()
    }

    return pd.concat([df, new], ignore_index=True), dwell, accrual_counters, len(new)


def apply_recovery(
    spec: DesignSpec, df: pd.DataFrame, *, previous: np.ndarray, current: np.ndarray
) -> pd.DataFrame:
    """Book the recovered share of the balance for entities writing off this period.

    Only entities *entering* a recovery state are booked. Without that test an
    absorbing state would book a recovery every period an entity sat in it, and
    a pool would recover several times what it lost.
    """
    rec = spec.dynamics.recovery
    assert rec is not None
    if rec.balance not in df.columns:
        raise AgeingError(
            f"dynamics.recovery reads {rec.balance!r}, which is not in the panel at this point"
        )

    entering = np.isin(current, rec.on_states) & (current != previous)
    # to_numpy before filling: a balance column can arrive as object dtype, and
    # pandas' own fillna would downcast it with a deprecation warning.
    balance = np.nan_to_num(pd.to_numeric(df[rec.balance], errors="coerce").to_numpy(dtype=float))
    booked = np.where(entering, np.round(balance * rec.rate, 2), 0.0)
    df[rec.target] = booked
    return df


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
    """Scale the probability of *worsening* under a scenario overlay.

    The same rescaling the configure form uses to hit a target default rate, so a
    scenario and a hand-set rate cannot drift into meaning different things.
    """
    lc = spec.lifecycle
    if lc is None or lc.transitions is None or scenario.default_multiplier == 1.0:
        return None if lc is None else lc.transitions

    from sdd.age.calibrate import scale_worsening

    return scale_worsening(lc.transitions, scenario.default_multiplier)


def _dpd_column(spec: DesignSpec) -> str | None:
    """Best guess at a days-past-due column, used to seed arrears counters."""
    for candidate in ("days_past_due", "dpd", "days_in_arrears"):
        if spec.column(candidate) or candidate in {d.target for d in spec.derivations}:
            return candidate
    return None

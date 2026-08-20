"""The portfolio, summarised at every cut-off.

The panel says what each loan did. A portfolio report says what the *book* did:
its size, its average coupon, how much of it is in trouble, how concentrated it
is. Those are the numbers an investor reads, and until now the engine reported
only a count of entities per state.

Computed inside the ageing loop, on the frame that is about to be written, so
the figures are of exactly the rows they describe. Recomputing them afterwards
from the panel would work and would be a second implementation of the same
arithmetic — which is how a report and the data it reports on drift apart.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from sdd.spec.schema import DesignSpec, Metric


class MetricError(ValueError):
    """A metric could not be computed from the columns available."""


def _selected(frame: pd.DataFrame, metric: Metric, period: int) -> pd.Series | None:
    """The `where` mask, or None when the metric takes every row."""
    if not metric.where:
        return None
    from sdd.generate.deriver import evaluate_mask

    try:
        return pd.Series(evaluate_mask(metric.where, frame, {"period": period}), index=frame.index)
    except Exception as exc:
        raise MetricError(
            f"metric {metric.name!r} could not evaluate {metric.where!r}: {exc}"
        ) from exc


def _numeric(frame: pd.DataFrame, column: str, metric: Metric) -> pd.Series:
    if column not in frame.columns:
        raise MetricError(f"metric {metric.name!r} reads {column!r}, which is not in the panel")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _one(frame: pd.DataFrame, metric: Metric, period: int, running: dict[str, float]) -> float:
    mask = _selected(frame, metric, period)
    kind = metric.kind

    if kind == "count":
        rows = frame if mask is None else frame[mask]
        return float(len(rows))

    if kind == "distinct_count":
        rows = frame if mask is None else frame[mask]
        if metric.column not in rows.columns:
            raise MetricError(
                f"metric {metric.name!r} reads {metric.column!r}, which is not in the panel"
            )
        return float(rows[metric.column].nunique())

    values = _numeric(frame, metric.column, metric)

    if kind == "sum":
        return float(values.sum() if mask is None else values[mask].sum())

    if kind == "cumulative":
        # A running total of a *flow*: a column that is zero except in the period
        # the event happens. Summing a stock column here would count the same
        # balance again every month.
        this_period = float(values.sum() if mask is None else values[mask].sum())
        running[metric.name] = running.get(metric.name, 0.0) + this_period
        return running[metric.name]

    if kind == "weighted_mean":
        weights = _numeric(frame, metric.weight, metric)
        if mask is not None:
            values, weights = values[mask], weights[mask]
        total = float(weights.sum())
        # Unweighted would treat a EUR 40m facility and a EUR 400k one alike; an
        # empty or zero-weight book has no average rather than a zero one.
        return float((values * weights).sum() / total) if total > 0 else float("nan")

    if kind == "share_where":
        assert mask is not None
        total = float(values.sum())
        return float(values[mask].sum() / total) if total > 0 else float("nan")

    if kind == "max_group_share":
        if metric.group not in frame.columns:
            raise MetricError(
                f"metric {metric.name!r} groups by {metric.group!r}, which is not in the panel"
            )
        rows = frame if mask is None else frame[mask]
        if rows.empty:
            return float("nan")
        by_group = _numeric(rows, metric.column, metric).groupby(rows[metric.group]).sum()
        total = float(by_group.sum())
        return float(by_group.max() / total) if total > 0 else float("nan")

    raise MetricError(f"metric {metric.name!r} has unknown kind {kind!r}")


def compute(
    spec: DesignSpec,
    frame: pd.DataFrame,
    period: int,
    date: str,
    running: dict[str, float],
) -> dict[str, Any]:
    """Every metric for one cut-off.

    ``running`` carries the cumulative totals between periods, which is the only
    state a metric can hold.
    """
    row: dict[str, Any] = {"period": period, "date": date}
    for metric in spec.metrics:
        value = _one(frame, metric, period, running)
        if metric.decimals is not None and not np.isnan(value):
            value = round(value, metric.decimals)
        row[metric.name] = value
    return row


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)

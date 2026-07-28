"""Read a sample tape and work out how it was put together.

This is the "it analyses it" half of the tool. Given a CSV or parquet file it
determines, for every column: what type it holds, whether it stays fixed for an
entity or moves over time, what values it takes, and which distribution best
describes it.

The two structural questions come first, because everything else depends on
them:

**Which column identifies an entity?**
    Looked for by name, then by behaviour — the column that is unique within
    each cut-off and largely repeats across them.

**Which column is the cut-off date?**
    The date-like column with few distinct values, each shared by many rows.

Get those two right and "static vs dynamic" falls out by counting distinct
values per entity. Get them wrong and every downstream inference is wrong, so
both are reported with a reason and can be overridden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sdd.profile.distributions import Fit, fit_categorical, fit_numeric, looks_discrete
from sdd.spec.schema import ColumnRole, DType

# Column names that give the game away, checked before any behavioural guess.
ID_NAME_HINTS = (
    "loan_id",
    "id",
    "identifier",
    "unique_id",
    "exposure_id",
    "account_id",
    "contract_id",
)
TIME_NAME_HINTS = (
    "reporting_date",
    "as_of",
    "as_of_date",
    "cutoff",
    "cut_off",
    "cut_off_date",
    "data_cut_off_date",
    "period",
    "snapshot_date",
    "observation_date",
)

# A string column with at most this share of distinct values is a category
# rather than free text.
MAX_CATEGORICAL_SHARE = 0.20
MAX_CATEGORICAL_VALUES = 200


@dataclass
class ColumnProfile:
    name: str
    dtype: DType
    role: ColumnRole
    nulls: float
    distinct: int
    examples: list[Any] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    domain: list[Any] | None = None
    fit: Fit | None = None
    note: str | None = None

    @property
    def confidence(self) -> float:
        return self.fit.confidence if self.fit else 0.5

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if k != "fit"}
        out["fit"] = self.fit.to_dict() if self.fit else None
        out["confidence"] = round(self.confidence, 3)
        return out


@dataclass
class DatasetProfile:
    rows: int
    columns: list[ColumnProfile]
    id_column: str | None
    time_column: str | None
    periods: int
    entities: int
    is_panel: bool
    detection_notes: dict[str, str] = field(default_factory=dict)
    dynamics: dict[str, Any] = field(default_factory=dict)
    derived: list[Any] = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def needs_review(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.confidence < 0.5]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "entities": self.entities,
            "periods": self.periods,
            "is_panel": self.is_panel,
            "id_column": self.id_column,
            "time_column": self.time_column,
            "detection_notes": self.detection_notes,
            "dynamics": self.dynamics,
            "derived": [
                {"target": d.target, "source": d.source, "confidence": d.confidence}
                for d in self.derived
            ],
            "needs_review": [c.name for c in self.needs_review],
            "columns": [c.to_dict() for c in self.columns],
        }

    def summary(self) -> str:
        shape = "panel" if self.is_panel else "single snapshot"
        lines = [
            f"{self.rows:,} rows x {len(self.columns)} columns ({shape})",
            f"  entity      {self.id_column or '(not detected)'}  -> {self.entities:,} entities",
            f"  time        {self.time_column or '(not detected)'}  -> {self.periods} period(s)",
        ]
        by_role: dict[str, int] = {}
        for c in self.columns:
            by_role[c.role] = by_role.get(c.role, 0) + 1
        lines.append("  roles       " + ", ".join(f"{v} {k}" for k, v in sorted(by_role.items())))
        if self.dynamics:
            learned = ", ".join(sorted(self.dynamics))
            lines.append(f"  learned     {learned}")
        if self.derived:
            pairs = ", ".join(f"{d.target}<-{d.source}" for d in self.derived[:4])
            more = f" (+{len(self.derived) - 4} more)" if len(self.derived) > 4 else ""
            lines.append(f"  derived     {pairs}{more}")
        if self.needs_review:
            names = ", ".join(c.name for c in self.needs_review[:6])
            more = f" (+{len(self.needs_review) - 6} more)" if len(self.needs_review) > 6 else ""
            lines.append(f"  review      {names}{more}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def read_sample(path: str | Path, *, max_rows: int | None = None) -> pd.DataFrame:
    """Load a CSV or parquet sample."""
    path = Path(path)
    if path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(path)
        return df.head(max_rows) if max_rows else df
    return pd.read_csv(path, nrows=max_rows, low_memory=False)


# ---------------------------------------------------------------------------
# structure detection
# ---------------------------------------------------------------------------


def detect_time_column(df: pd.DataFrame) -> tuple[str | None, str]:
    """Find the cut-off date column.

    A cut-off column has few distinct values, each shared by many rows — the
    opposite shape to an origination or maturity date, which is roughly unique
    per loan. That distinction is what stops the profiler from treating
    ``maturity_date`` as the panel's time axis.
    """
    for name in TIME_NAME_HINTS:
        if name in df.columns:
            return name, f"matched the known cut-off name {name!r}"

    best: tuple[str, int] | None = None
    for col in df.columns:
        series = df[col].dropna()
        if series.empty or not _looks_like_date(series):
            continue
        distinct = series.nunique()
        # Few distinct values, each covering many rows.
        if distinct < 2 or distinct > max(len(df) // 10, 2):
            continue
        if best is None or distinct < best[1]:
            best = (col, distinct)

    if best:
        return best[0], f"date-like with only {best[1]} distinct values, each shared by many rows"
    return None, "no column looked like a repeated cut-off date"


def detect_id_column(df: pd.DataFrame, time_column: str | None) -> tuple[str | None, str]:
    """Find the entity identifier."""
    for name in ID_NAME_HINTS:
        if name in df.columns:
            return name, f"matched the known identifier name {name!r}"

    candidates: list[tuple[str, float]] = []
    for col in df.columns:
        if col == time_column:
            continue
        series = df[col]
        if series.isna().any():
            continue
        if time_column and time_column in df.columns:
            # Unique inside every cut-off is the defining property.
            per_period = df.groupby(time_column)[col].agg(["nunique", "size"])
            if not (per_period["nunique"] == per_period["size"]).all():
                continue
            # Prefer the column that recurs most across periods.
            recurrence = len(df) / max(series.nunique(), 1)
            candidates.append((col, recurrence))
        elif series.is_unique:
            candidates.append((col, 1.0))

    if candidates:
        best = max(candidates, key=lambda c: c[1])
        return best[0], "unique within every cut-off and recurring across them"
    return None, "no column was unique within each cut-off"


def _looks_like_date(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.astype(str).head(200)
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        return False
    return bool(parsed.notna().mean() > 0.9)


# ---------------------------------------------------------------------------
# per-column profiling
# ---------------------------------------------------------------------------


def infer_dtype(series: pd.Series) -> DType:
    clean = series.dropna()
    if clean.empty:
        return "str"
    if pd.api.types.is_bool_dtype(clean):
        return "bool"
    if pd.api.types.is_numeric_dtype(clean):
        return "int" if looks_discrete(clean) and clean.nunique() > 2 else "float"
    if _looks_like_date(clean):
        return "date"
    return "category" if _is_categorical(clean) else "str"


def _is_categorical(series: pd.Series) -> bool:
    distinct = series.nunique()
    if distinct > MAX_CATEGORICAL_VALUES:
        return False
    return distinct / max(len(series), 1) <= MAX_CATEGORICAL_SHARE or distinct <= 30


def infer_role(
    df: pd.DataFrame, column: str, id_column: str | None, is_panel: bool
) -> tuple[ColumnRole, str | None]:
    """Static, dynamic, or constant — decided by counting, not by guessing.

    Without a panel there is no way to tell static from dynamic (nothing has
    been observed twice), so everything non-constant is called static and the
    ambiguity is recorded rather than papered over.
    """
    series = df[column]
    if series.nunique(dropna=False) <= 1:
        return "constant", None
    if not is_panel or not id_column:
        return (
            "static",
            "single snapshot: cannot tell static from dynamic without repeat observations",
        )
    changing = df.groupby(id_column)[column].nunique(dropna=False)
    return ("static", None) if (changing <= 1).all() else ("dynamic", None)


def profile_column(
    df: pd.DataFrame,
    column: str,
    id_column: str | None,
    is_panel: bool,
    first_period: pd.DataFrame | None = None,
) -> ColumnProfile:
    series = df[column]
    dtype = infer_dtype(series)
    role, note = infer_role(df, column, id_column, is_panel)

    # Generators are fitted to the *first* cut-off, never to the pooled panel,
    # because what they produce is the opening book. Two distinct errors follow
    # from pooling, and both compound through the ageing pass:
    #
    #   dynamic columns — seasoning_months pooled over 24 cut-offs is smeared 24
    #     months wider than it is on day one, so every loan starts in the wrong
    #     place;
    #   static columns — a loan that redeems in month 2 contributes 2 rows while
    #     a survivor contributes 24, so the pooled mix is weighted by survival
    #     rather than by origination. Any characteristic correlated with
    #     prepayment or default is silently skewed.
    source = df
    if first_period is not None and not first_period.empty:
        source = first_period
        note = note or "generator fitted to the first cut-off, which is what the opening book is"
    clean = source[column].dropna()

    profile = ColumnProfile(
        name=column,
        dtype=dtype,
        role=role,
        nulls=round(float(series.isna().mean()), 6),
        distinct=int(series.nunique(dropna=True)),
        examples=[_plain(v) for v in clean.head(3).tolist()],
        note=note,
    )

    if dtype in ("int", "float") and not clean.empty:
        numeric = pd.to_numeric(clean, errors="coerce").dropna()
        if not numeric.empty:
            profile.minimum = round(float(numeric.min()), 6)
            profile.maximum = round(float(numeric.max()), 6)
            profile.mean = round(float(numeric.mean()), 6)

    if role == "constant":
        from sdd.spec.schema import ConstantGen

        value = _plain(clean.iloc[0]) if not clean.empty else None
        profile.fit = Fit(ConstantGen(value=value), "constant", 0.0, 1.0)
    elif dtype in ("category", "bool", "str"):
        if dtype == "str" and not _is_categorical(clean):
            # Free text or a per-row identifier: resampling it would leak the
            # sample verbatim, so hand back a synthetic id instead.
            from sdd.spec.schema import UUIDGen

            profile.fit = Fit(
                UUIDGen(short=True),
                "uuid",
                0.0,
                0.3,
                note="high-cardinality text; replaced with a synthetic identifier rather than "
                "resampled, which would copy sample values verbatim",
            )
        else:
            profile.fit = fit_categorical(clean)
            # The domain comes from the *whole* panel even though the generator
            # comes from period 0. They answer different questions: the
            # generator seeds the opening book, while the domain constrains
            # every period. A delinquency column has no Redeemed loans on day
            # one but certainly acquires them, and a domain drawn from period 0
            # would make the validator reject the engine's own correct output.
            everywhere = df[column].dropna()
            profile.domain = [_plain(v) for v in sorted(everywhere.unique(), key=str)][
                :MAX_CATEGORICAL_VALUES
            ]
    elif dtype == "date":
        profile.fit = fit_categorical(clean.astype(str))
        profile.fit.confidence = 0.6
        profile.fit.note = "dates resampled from observed values; consider a calendar rule instead"
    else:
        profile.fit = fit_numeric(clean)

    return profile


# ---------------------------------------------------------------------------
# the whole dataset
# ---------------------------------------------------------------------------


def profile_dataset(
    data: str | Path | pd.DataFrame,
    *,
    id_column: str | None = None,
    time_column: str | None = None,
    state_column: str | None = None,
    max_rows: int | None = None,
    learn_dynamics: bool = True,
) -> DatasetProfile:
    """Profile a sample tape or panel.

    ``id_column`` / ``time_column`` override detection when the heuristics get
    it wrong, which is why both report the reason they chose what they did.
    """
    df = data if isinstance(data, pd.DataFrame) else read_sample(data, max_rows=max_rows)
    if df.empty:
        raise ValueError("the sample is empty; nothing to profile")

    notes: dict[str, str] = {}
    if time_column is None:
        time_column, why = detect_time_column(df)
        notes["time_column"] = why
    else:
        notes["time_column"] = "supplied by the caller"

    if id_column is None:
        id_column, why = detect_id_column(df, time_column)
        notes["id_column"] = why
    else:
        notes["id_column"] = "supplied by the caller"

    periods = int(df[time_column].nunique()) if time_column else 1
    entities = int(df[id_column].nunique()) if id_column else len(df)
    is_panel = bool(time_column and id_column and periods > 1)

    first_period = (
        df[df[time_column] == df[time_column].min()] if is_panel and time_column else None
    )
    columns = [profile_column(df, c, id_column, is_panel, first_period) for c in df.columns]

    profile = DatasetProfile(
        rows=len(df),
        columns=columns,
        id_column=id_column,
        time_column=time_column,
        periods=periods,
        entities=entities,
        is_panel=is_panel,
        detection_notes=notes,
    )

    if is_panel and learn_dynamics:
        from sdd.profile.panel import learn_panel_dynamics

        profile.dynamics = learn_panel_dynamics(df, profile, state_column=state_column)

    # Columns computed from other columns are re-expressed as derivations, so
    # the generated data stays internally consistent instead of pairing a
    # balance with a band that does not contain it.
    from sdd.profile.derived import find_bucket_columns

    buckets = find_bucket_columns(df, profile)
    if buckets:
        profile.derived = buckets
        by_name = {b.target: b for b in buckets}
        for col in profile.columns:
            if col.name in by_name:
                col.role = "derived"
                col.fit = None
                col.note = by_name[col.name].note

    return profile


def _plain(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value

"""The best score any model could achieve on a generated portfolio.

Every other module here helps you make data that looks real. This one answers a
question real data structurally cannot.

**Why it matters.** A credit model scores 0.84 on a bank's book. Is that good?
Nobody knows, because nobody knows what was achievable — the bank's data has a
ceiling too, and it is unobservable. Every argument about model quality on real
data is therefore an argument about an unknown denominator.

On data whose generating process is *declared*, the denominator is computable.
0.84 becomes "0.84 against a ceiling of 0.87" — a statement about the model
instead of a number floating free.

Two bounds, answering different questions:

**Oracle.** What a model that could see the hidden driver would score. Nothing
can beat it. The gap between it and the ceiling is information the observables
simply do not carry, however clever the model.

**Ceiling.** The best obtainable from the observables alone. This is the honest
target, and it is what a model should be measured against.

The method is Bayes' rule, not simulation. Each observable is its latent group's
centre plus independent noise of stated width, so the posterior over the hidden
driver given the observables is exact, and the optimal score is the posterior
mean of the outcome rate. Being derived rather than estimated is the point: an
approximated ceiling would be one more number to argue about, and the whole
purpose is to end an argument.

**The check that keeps it honest.** A ceiling is a claim, and a claim that
cannot fail is worthless. If any model beats the ceiling, the ceiling is wrong —
so `compare()` reports that as a failure rather than as a good result.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sdd.spec.schema import DesignSpec

# Enough rows that P(bad | latent) is measured rather than guessed. The oracle
# pass is cheap and runs once.
ORACLE_ROWS = 40_000

# How far above the ceiling counts as impossible rather than as sampling noise.
#
# A model that is genuinely Bayes-optimal lands *either side* of the empirical
# ceiling on a finite sample: both are estimates of the same population
# quantity, and they break ties between rows differently. Measured, logistic
# regression on this benchmark sits within a whisker of the ceiling and
# sometimes a hair above it. Roughly one standard error of an AUC at benchmark
# sizes, so a real violation still shows and arithmetic noise does not. The raw
# gap is always reported, so a reader can judge a marginal case themselves.
CEILING_TOLERANCE = 0.005


class BenchmarkError(ValueError):
    """The spec does not describe an invertible generating process."""


@dataclass
class Ceiling:
    """What was achievable, and what the observables cost you."""

    ceiling: float
    oracle: float
    outcome_rate: float
    latent_risk: dict[str, float]
    scores: pd.Series = field(repr=False)
    labels: pd.Series = field(repr=False)

    @property
    def observable_cost(self) -> float:
        """How much the noise in the observables costs, in AUC.

        The distance between seeing the truth and inferring it perfectly. No
        model closes this gap; it is a property of the data, not of the model.
        """
        return self.oracle - self.ceiling

    def summary(self) -> str:
        return (
            f"ceiling {self.ceiling:.4f} · oracle {self.oracle:.4f} "
            f"(observables cost {self.observable_cost:.4f}) · "
            f"outcome rate {self.outcome_rate:.3%}"
        )


def label_outcome(spec: DesignSpec, panel: pd.DataFrame) -> pd.Series:
    """1 where an entity ever reached one of the benchmark's bad states.

    Indexed by entity and taken over the whole panel, so the label is "did this
    borrower go bad at any point", which is the question a credit model is
    normally asked.
    """
    bench = _bench(spec)
    id_column = spec.entity.id_column
    state = spec.lifecycle.state_column if spec.lifecycle else None
    if state is None or state not in panel.columns:
        raise BenchmarkError(f"the panel has no {state!r} column to read an outcome from")

    bad = set(panel.loc[panel[state].isin(bench.label_states), id_column])
    entities = _plain_index(pd.Index(panel[id_column].unique()).sort_values(), id_column)
    return pd.Series(entities.isin(bad).astype(int), index=entities, name="outcome")


def observables(spec: DesignSpec, panel: pd.DataFrame) -> pd.DataFrame:
    """One row per entity, holding only what a model is allowed to see.

    Read at the first cut-off: a score is made at a point in time, and letting a
    model read later cut-offs would leak the outcome it is being asked to
    predict.
    """
    bench = _bench(spec)
    id_column, time_column = spec.entity.id_column, spec.entity.time_column
    opening = panel[panel[time_column] == panel[time_column].min()]
    columns = [o.column for o in bench.observables]
    missing = [c for c in columns if c not in panel.columns]
    if missing:
        raise BenchmarkError(f"the panel is missing declared observables: {missing}")

    seen = opening.set_index(id_column)[columns].sort_index()
    seen.index = _plain_index(seen.index, id_column)
    return seen


def _plain_index(index: pd.Index, name: str) -> pd.Index:
    """An index scikit-learn can slice.

    Parquet round-trips identifiers back as Arrow-backed strings under pandas 3,
    and `train_test_split` indexes its inputs positionally with an integer
    array — which an Arrow-backed index refuses, with
    `TypeError: only integer scalar arrays can be converted to a scalar index`.

    Found in CI rather than here: pandas 2 hands back a plain object index for
    the same file, so the same code passed locally and failed on three Python
    versions. Normalised at the boundary so callers never meet it, since the
    obvious thing to do with these frames is hand them to scikit-learn.

    ``dtype=object`` is load-bearing and was missed on the first attempt.
    Pandas 3 infers a string dtype for an object array of strings, so
    ``pd.Index(values.to_numpy(dtype=object))`` returns straight back to the
    Arrow-backed dtype this exists to escape — the conversion looks right,
    changes nothing, and the failure is identical. Stating the dtype is what
    makes it stick.
    """
    return pd.Index(index.to_numpy(dtype=object), dtype=object, name=name)


def ceiling(
    spec: str | Path | dict[str, Any] | DesignSpec,
    panel: pd.DataFrame,
    *,
    oracle_rows: int = ORACLE_ROWS,
    seed: int = 99,
) -> Ceiling:
    """The best achievable score on ``panel``, and the oracle bound above it.

    ``spec`` must be the one the panel came from: the emission model is read
    from it, so a mismatched spec computes a ceiling for data that does not
    exist.
    """
    from sklearn.metrics import roc_auc_score

    loaded = spec if isinstance(spec, DesignSpec) else _load(spec)
    _bench(loaded)  # fail here, with a readable reason, rather than deep in the maths

    risk = _latent_risk(loaded, oracle_rows=oracle_rows, seed=seed)
    labels = label_outcome(loaded, panel)
    seen = observables(loaded, panel).reindex(labels.index)

    posterior = _posterior(loaded, seen)
    order = list(risk.index)
    scores = pd.Series(posterior @ risk.reindex(order).to_numpy(), index=seen.index)

    return Ceiling(
        ceiling=float(roc_auc_score(labels, scores)),
        oracle=float(_oracle(loaded, oracle_rows=oracle_rows, seed=seed)),
        outcome_rate=float(labels.mean()),
        latent_risk={str(k): float(v) for k, v in risk.items()},
        scores=scores,
        labels=labels,
    )


def compare(
    spec: str | Path | dict[str, Any] | DesignSpec,
    panel: pd.DataFrame,
    model_scores: pd.Series,
    *,
    name: str = "model",
    **kwargs: Any,
) -> dict[str, Any]:
    """Score a model against the ceiling, and say what the number means.

    ``model_scores`` is indexed by entity, higher meaning more likely to go bad.

    Reports `beat_the_ceiling` as a **problem**. A model cannot legitimately
    exceed the best achievable score, so if one does, the ceiling is wrong or
    the model saw something it should not have — and either is a finding worth
    more than the score itself.

    In practice the usual cause is scoring in-sample. It caught exactly that
    during this module's own development: a gradient booster fitted and scored
    on the same rows reached 0.93 against a ceiling of 0.897, which is not a
    good model but a memorised one. **Score out of sample.**
    """
    from sklearn.metrics import roc_auc_score

    result = ceiling(spec, panel, **kwargs)
    aligned = model_scores.reindex(result.labels.index)
    if aligned.isna().any():
        raise BenchmarkError(
            f"{aligned.isna().sum()} entities have no model score; the series must be "
            "indexed by entity and cover every row being scored"
        )

    achieved = float(roc_auc_score(result.labels, aligned))
    span = result.ceiling - 0.5
    return {
        "name": name,
        "achieved": achieved,
        "ceiling": result.ceiling,
        "oracle": result.oracle,
        "captured": (achieved - 0.5) / span if span > 0 else float("nan"),
        "gap_to_ceiling": result.ceiling - achieved,
        "beat_the_ceiling": achieved > result.ceiling + CEILING_TOLERANCE,
        "outcome_rate": result.outcome_rate,
    }


# ---------------------------------------------------------------------------
# the inversion
# ---------------------------------------------------------------------------


def _posterior(spec: DesignSpec, seen: pd.DataFrame) -> np.ndarray:
    """P(latent | observables), exactly.

    Independent Gaussian readings, so the log-posterior is the log-prior plus
    one log-density per observable. Accumulated in logs because five tiers times
    three densities underflows to zero in the tails otherwise, and the tails are
    where the risky borrowers are.
    """
    from scipy.stats import norm

    bench = _bench(spec)
    prior = np.asarray(_latent_prior(spec), dtype=float)
    log_posterior = np.log(prior)[None, :].repeat(len(seen), axis=0)

    for observable in bench.observables:
        centres = _centres(spec, observable.centres)
        width = _noise_width(spec, observable.noise)
        values = pd.to_numeric(seen[observable.column], errors="coerce").to_numpy()
        log_posterior = log_posterior + norm.logpdf(
            values[:, None], loc=centres[None, :], scale=width
        )

    log_posterior -= log_posterior.max(axis=1, keepdims=True)
    posterior = np.exp(log_posterior)
    return posterior / posterior.sum(axis=1, keepdims=True)


def _latent_risk(spec: DesignSpec, *, oracle_rows: int, seed: int) -> pd.Series:
    """P(bad | latent), measured by generating once with the latent exposed.

    Measured rather than derived from the transition matrix, and deliberately.
    The outcome depends on the matrix, the stress coupling, the horizon and the
    terminal states together, and an analytic version would be a second
    implementation of the ageing engine — which is exactly how a figure and the
    thing it describes drift apart. Generating once with the driver visible uses
    the engine itself as the source of truth.
    """
    from sdd import api

    exposed, latent = _expose_latent(spec)
    result = api.run(exposed, oracle_rows, _scratch(), seed=seed, validate_output=False)
    panel = pd.read_parquet(result["panel"])

    labels = label_outcome(spec, panel)
    opening = panel[panel[spec.entity.time_column] == panel[spec.entity.time_column].min()]
    tiers = opening.set_index(spec.entity.id_column)[latent].reindex(labels.index)
    risk = labels.groupby(tiers).mean()
    return risk.reindex(_latent_values(spec)).fillna(0.0)


def _oracle(spec: DesignSpec, *, oracle_rows: int, seed: int) -> float:
    """What a model that could see the hidden driver would score."""
    from sklearn.metrics import roc_auc_score

    from sdd import api

    exposed, latent = _expose_latent(spec)
    result = api.run(exposed, oracle_rows, _scratch(), seed=seed, validate_output=False)
    panel = pd.read_parquet(result["panel"])

    labels = label_outcome(spec, panel)
    opening = panel[panel[spec.entity.time_column] == panel[spec.entity.time_column].min()]
    tiers = opening.set_index(spec.entity.id_column)[latent].reindex(labels.index)
    risk = labels.groupby(tiers).mean()
    return float(roc_auc_score(labels, tiers.map(risk)))


def _expose_latent(spec: DesignSpec) -> tuple[dict[str, Any], str]:
    """The same spec with the hidden driver emitted.

    Only the pack's author can do this, which is the whole asymmetry the
    instrument rests on: you know the answer, the model does not.
    """
    latent = _bench(spec).latent
    dumped = copy.deepcopy(spec.model_dump(mode="json", exclude_none=True, by_alias=True))
    for column in dumped.get("columns", []):
        if column.get("name") == latent:
            column["role"] = "static"
            break
    else:
        raise BenchmarkError(f"the spec has no column {latent!r} to expose")
    dumped.pop("emit", None)
    return dumped, latent


# ---------------------------------------------------------------------------
# reading the declared model
# ---------------------------------------------------------------------------


def _bench(spec: DesignSpec) -> Any:
    if spec.benchmark is None:
        raise BenchmarkError(
            "this spec declares no `benchmark` block, so its generating process cannot be "
            "inverted and no ceiling exists. See packs/credit_benchmark_known_ceiling.yaml"
        )
    return spec.benchmark


def _latent_column(spec: DesignSpec) -> Any:
    latent = _bench(spec).latent
    column = next((c for c in spec.columns if c.name == latent), None)
    if column is None:
        raise BenchmarkError(f"the spec has no column {latent!r}")
    if column.role != "helper":
        raise BenchmarkError(
            f"{latent!r} has role {column.role!r}, so it reaches the output. A driver a "
            "model can read is not hidden, and the ceiling would be meaningless"
        )
    return column


def _latent_values(spec: DesignSpec) -> list[str]:
    column = _latent_column(spec)
    values = column.domain or getattr(column.generator, "values", None)
    if not values:
        raise BenchmarkError(f"{column.name!r} declares no domain, so its values are unknown")
    return [str(v) for v in values]


def _latent_prior(spec: DesignSpec) -> list[float]:
    generator = _latent_column(spec).generator
    weights = getattr(generator, "weights", None)
    values = _latent_values(spec)
    if not weights:
        return [1.0 / len(values)] * len(values)
    total = float(sum(weights))
    return [float(w) / total for w in weights]


def _centres(spec: DesignSpec, target: str) -> np.ndarray:
    """One mean per latent value, read off the `when` rules in declared order."""
    derivation = next((d for d in spec.derivations if d.target == target), None)
    if derivation is None or not derivation.rules:
        raise BenchmarkError(f"no `when` derivation named {target!r} to read centres from")
    centres = [float(rule.then) for rule in derivation.rules] + [float(derivation.else_)]
    expected = len(_latent_values(spec))
    if len(centres) != expected:
        raise BenchmarkError(
            f"{target!r} gives {len(centres)} centres for {expected} latent values; every "
            "value needs one, in the order the domain declares them"
        )
    return np.asarray(centres, dtype=float)


def _noise_width(spec: DesignSpec, column_name: str) -> float:
    column = next((c for c in spec.columns if c.name == column_name), None)
    if column is None:
        raise BenchmarkError(f"the spec has no noise column {column_name!r}")
    width = getattr(column.generator, "stddev", None)
    if not width:
        raise BenchmarkError(
            f"{column_name!r} is not a gaussian with a `stddev`, so its width is unknown "
            "and the emission model cannot be inverted"
        )
    return float(width)


def _load(spec: Any) -> DesignSpec:
    from sdd import api

    return api.load(spec)


def _scratch() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="sdd-benchmark-"))

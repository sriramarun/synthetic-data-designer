"""The ceiling: the best score any model could achieve on generated data.

Every other test here asks whether the data is internally consistent or looks
plausible. These ask whether the *measurement* is sound, because the whole claim
rests on it: if the ceiling can be beaten, it is not a ceiling, and the number
is worse than useless — it is a number people would quote.

So the tests that matter are the ones that could fail. A strong model is trained
and must land below the ceiling; a model that cheats must be caught; and a
generating process that cannot be inverted must be refused rather than
approximated.
"""

from __future__ import annotations

import copy
import pathlib
import tempfile

import pandas as pd
import pytest

from sdd import api, benchmark

PACK = "credit_benchmark_known_ceiling"
ENTITIES = 12_000
SEED = 7


@pytest.fixture(scope="module")
def generated():
    tmp = pathlib.Path(tempfile.mkdtemp())
    result = api.run(PACK, ENTITIES, tmp, seed=SEED, validate_output=False)
    return api.load(PACK), pd.read_parquet(result["panel"])


@pytest.fixture(scope="module")
def known(generated):
    spec, panel = generated
    return benchmark.ceiling(spec, panel)


# ---------------------------------------------------------------------------
# the hidden driver must actually be hidden
# ---------------------------------------------------------------------------


def test_an_arrow_backed_panel_can_still_be_split(generated):
    """A panel read back under pandas 3, handed to scikit-learn.

    Parquet returns identifiers as Arrow-backed strings there, and
    `train_test_split` indexes positionally with an integer array — which an
    Arrow-backed index refuses outright. The same code passed on pandas 2 and
    failed on all three Python versions in CI, so the dtype is forced here to
    keep the regression reproducible on either version.
    """
    from sklearn.model_selection import train_test_split

    spec, panel = generated
    arrow = panel.copy()
    arrow[spec.entity.id_column] = arrow[spec.entity.id_column].astype("string[pyarrow]")

    features = benchmark.observables(spec, arrow)
    labels = benchmark.label_outcome(spec, arrow)
    train, test = train_test_split(features.index, test_size=0.3, random_state=0, stratify=labels)

    assert len(train) and len(test)
    assert not set(train) & set(test)
    assert benchmark.ceiling(spec, arrow).ceiling > 0.5


def test_the_driver_never_reaches_the_output(generated):
    """The asymmetry the whole instrument rests on.

    If the driver leaked, a model could read the answer and the ceiling would
    describe nothing.
    """
    spec, panel = generated
    assert spec.benchmark.latent not in panel.columns

    hidden = [c.name for c in spec.columns if c.role == "helper"]
    assert hidden, "nothing is hidden, so there is no latent to infer"
    for name in hidden:
        assert name not in panel.columns, f"{name} leaked into the panel"


def test_a_visible_driver_is_refused(generated):
    """Loud, not lenient. A ceiling computed against a readable driver would be
    arithmetically fine and completely meaningless."""
    spec, panel = generated
    exposed = spec.model_dump(mode="json", exclude_none=True, by_alias=True)
    next(c for c in exposed["columns"] if c["name"] == "risk_tier")["role"] = "static"

    with pytest.raises(benchmark.BenchmarkError, match=r"not hidden|reaches the output"):
        benchmark.ceiling(exposed, panel)


def test_a_spec_with_no_benchmark_block_says_so(tmp_path):
    """Most packs are not instruments, and asking one for a ceiling should
    explain that rather than fail somewhere in the arithmetic."""
    result = api.run("auto_abs_esma_annex5", 200, tmp_path, seed=1, validate_output=False)
    panel = pd.read_parquet(result["panel"])

    with pytest.raises(benchmark.BenchmarkError, match="declares no `benchmark` block"):
        benchmark.ceiling("auto_abs_esma_annex5", panel)


# ---------------------------------------------------------------------------
# the ceiling has to bind
# ---------------------------------------------------------------------------


def test_the_ceiling_sits_between_chance_and_the_oracle(known):
    """The three numbers have a required order, and it is not decorative.

    A ceiling below chance means the inversion has the sign wrong. A ceiling
    above the oracle means the observables carry more than the truth they are
    readings of, which is impossible.
    """
    assert 0.5 < known.ceiling <= known.oracle + benchmark.CEILING_TOLERANCE
    assert known.oracle <= 1.0
    assert known.observable_cost >= -benchmark.CEILING_TOLERANCE


def test_a_trained_model_does_not_beat_it(generated, known):
    """The test that decides whether any of this is worth having.

    A gradient booster with four hundred trees is given every observable and a
    proper out-of-sample split. It should get close — the ceiling is meant to be
    approachable — and it must not exceed it.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import train_test_split

    spec, panel = generated
    features = benchmark.observables(spec, panel)
    labels = benchmark.label_outcome(spec, panel)

    train, test = train_test_split(features.index, test_size=0.4, random_state=0, stratify=labels)
    held_out = panel[panel[spec.entity.id_column].isin(set(test))]

    model = HistGradientBoostingClassifier(max_iter=400, random_state=0)
    model.fit(features.loc[train], labels.loc[train])
    scores = pd.Series(model.predict_proba(features.loc[test])[:, 1], index=test)

    report = benchmark.compare(spec, held_out, scores, name="gbm")
    assert not report["beat_the_ceiling"], report
    assert report["achieved"] > 0.75, "the benchmark should be learnable, not hopeless"
    assert report["captured"] > 0.85, report


def test_a_single_feature_falls_short(generated, known):
    """If one observable were enough, combining them would be pointless and the
    ceiling would be a restatement of the best column."""
    spec, panel = generated
    features = benchmark.observables(spec, panel)
    labels = benchmark.label_outcome(spec, panel)

    from sklearn.metrics import roc_auc_score

    best_single = max(roc_auc_score(labels, -features[column]) for column in features.columns)
    assert best_single < known.ceiling - 0.01, (
        f"a single feature reaches {best_single:.4f} against a ceiling of {known.ceiling:.4f}; "
        "the observables are not carrying independent information"
    )


# ---------------------------------------------------------------------------
# negative controls
# ---------------------------------------------------------------------------


def test_cheating_is_caught(generated):
    """The control that proves the check can fail.

    Scored on the rows it was fitted to, a flexible model memorises and clears
    the ceiling. That is impossible, so it must be reported — this is the exact
    mistake the check caught during development, reproduced on purpose.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    spec, panel = generated
    features = benchmark.observables(spec, panel)
    labels = benchmark.label_outcome(spec, panel)

    leaky = HistGradientBoostingClassifier(max_iter=400, random_state=0)
    leaky.fit(features, labels)
    in_sample = pd.Series(leaky.predict_proba(features)[:, 1], index=features.index)

    report = benchmark.compare(spec, panel, in_sample, name="in-sample")
    assert report["beat_the_ceiling"], (
        "a model scored on its own training rows cleared the ceiling and was not "
        "flagged, which is the one failure this check exists to prevent"
    )


def test_noisier_observables_lower_the_ceiling(generated, tmp_path):
    """The difficulty dial, which is what makes this an instrument.

    Doubling the measurement error must lower what is achievable while leaving
    the oracle alone — the hidden driver has not changed, only how well it can
    be read.
    """
    spec, _ = generated
    noisier = copy.deepcopy(spec.model_dump(mode="json", exclude_none=True, by_alias=True))
    for column in noisier["columns"]:
        if column["name"].startswith("_noise_"):
            column["generator"]["stddev"] *= 2.5

    panel = pd.read_parquet(
        api.run(noisier, ENTITIES, tmp_path / "noisy", seed=SEED, validate_output=False)["panel"]
    )
    degraded = benchmark.ceiling(noisier, panel)
    baseline = benchmark.ceiling(spec, _panel(tmp_path / "base"))

    assert degraded.ceiling < baseline.ceiling - 0.02, (
        f"noise 2.5x left the ceiling at {degraded.ceiling:.4f} against "
        f"{baseline.ceiling:.4f}; the dial does nothing"
    )
    assert abs(degraded.oracle - baseline.oracle) < 0.03, "the oracle should barely move"


def _panel(out) -> pd.DataFrame:
    return pd.read_parquet(api.run(PACK, ENTITIES, out, seed=SEED, validate_output=False)["panel"])


# ---------------------------------------------------------------------------
# the declared model has to be readable
# ---------------------------------------------------------------------------


def test_the_emission_model_is_read_from_the_spec(generated, known):
    """The centres and widths come out of the YAML, not out of the code.

    That is what lets someone build their own instrument, and it is what makes
    the difficulty dial work at all.
    """
    spec, _ = generated
    tiers = next(c for c in spec.columns if c.name == spec.benchmark.latent).domain
    assert set(known.latent_risk) == set(map(str, tiers))

    risks = [known.latent_risk[str(t)] for t in tiers]
    assert risks == sorted(risks), f"risk should rise across the tiers in order: {risks}"


def test_a_missing_centre_is_refused(generated):
    """Half a description of the generating process cannot be inverted, and
    guessing the rest would produce a plausible wrong answer."""
    spec, panel = generated
    broken = spec.model_dump(mode="json", exclude_none=True, by_alias=True)
    rules = next(d for d in broken["derivations"] if d["target"] == "_centre_score")["rules"]
    rules.pop()

    with pytest.raises(benchmark.BenchmarkError, match="centres for"):
        benchmark.ceiling(broken, panel)

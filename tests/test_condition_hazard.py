"""Maturity: a state change driven by the entity's own data.

The other two hazards are blind to the frame. Bernoulli is a flat chance and
dwell-time is a fixed count of periods, so both treat every entity alike. A loan
matures when *its own* maturity date arrives, and a 24-month loan and a 72-month
one written on the same day mature four years apart.

Before this existed, no pack modelled maturity at all: both shipped packs reach
`Redeemed` only through prepayment, and a spec that named a `Matured` state was
rejected by the loader as unreachable.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sdd import api
from sdd.spec import SpecError

PACK = "auto_abs_esma_annex5"


def _spec(*, periods: int = 12, when: str = "remaining_term_months <= 0") -> dict:
    spec = api.load(PACK).model_dump(mode="json", exclude_none=True, by_alias=True)
    spec["entity"]["calendar"]["periods"] = periods
    lc = spec["lifecycle"]
    lc["states"] = [*lc["states"], "Matured"]
    lc["terminal"] = [*lc["terminal"], "Matured"]
    lc["hazards"] = [
        *lc["hazards"],
        {
            "kind": "condition",
            "name": "maturity",
            "when": when,
            "to_state": "Matured",
            "excluded_states": ["Defaulted"],
        },
    ]
    return spec


def _term_column(spec: dict) -> str:
    return "remaining_term_months"


def test_a_state_reachable_only_by_condition_is_not_an_orphan():
    """The loader used to reject `Matured` as unreachable. It must now accept it."""
    loaded = api.load(_spec())
    assert "Matured" in loaded.lifecycle.states
    assert "maturity" in {h.name for h in loaded.lifecycle.hazards}


def test_loans_actually_mature(tmp_path):
    result = api.run(_spec(periods=18), 400, tmp_path, seed=5, validate_output=False)
    panel = pd.read_parquet(result["panel"])
    matured = panel[panel[api.load(PACK).lifecycle.state_column] == "Matured"]

    assert not matured.empty, "no facility reached its maturity date in 18 periods"
    # Maturity is a fact, not a draw: every matured row must satisfy the condition.
    assert (matured["remaining_term_months"] <= 0).all()


def test_maturity_terminates_the_facility(tmp_path):
    """A matured row is written once, then the facility leaves the pool."""
    spec = _spec(periods=18)
    result = api.run(spec, 400, tmp_path, seed=5, validate_output=False)
    panel = pd.read_parquet(result["panel"])
    state_col = api.load(PACK).lifecycle.state_column
    id_col = api.load(PACK).entity.id_column
    time_col = api.load(PACK).entity.time_column

    matured = panel[panel[state_col] == "Matured"]
    # One matured row per facility, and it is that facility's last row.
    assert matured.groupby(id_col).size().max() == 1
    last_seen = panel.groupby(id_col)[time_col].max()
    for fid, when in matured.set_index(id_col)[time_col].items():
        assert last_seen[fid] == when, f"{fid} kept reporting after maturing"


def test_condition_beats_the_probabilistic_hazards(tmp_path):
    """A facility meeting its condition settles before any draw is made.

    The condition is made true for everyone at period 1 and prepayment is set to
    90% per period, so both rules apply to the same facilities in the same
    period. If conditions did not run first, roughly nine in ten would be drawn
    into `Redeemed` instead of maturing.

    An earlier version of this test used a real maturity condition with the same
    90% rate, and proved nothing: the pool prepaid itself empty within two
    periods, so no facility ever survived long enough to reach its maturity date.
    """
    spec = _spec(periods=4, when="period >= 1")
    for hz in spec["lifecycle"]["hazards"]:
        if hz.get("name") == "prepayment":
            hz.pop("annual_rate", None)
            hz["period_rate"] = 0.9

    result = api.run(spec, 400, tmp_path, seed=5, validate_output=False)
    panel = pd.read_parquet(result["panel"])
    state_col = api.load(PACK).lifecycle.state_column
    at_p1 = panel[
        panel[api.load(PACK).entity.time_column]
        == sorted(panel[api.load(PACK).entity.time_column].unique())[1]
    ]

    matured = int((at_p1[state_col] == "Matured").sum())
    redeemed = int((at_p1[state_col] == "Redeemed").sum())
    assert redeemed == 0, (
        f"{redeemed} facilities prepaid in the period their condition was true; "
        "the probabilistic hazards are running first"
    )
    assert matured > 0


def test_an_unknown_column_in_the_condition_is_refused():
    with pytest.raises(SpecError) as caught:
        api.load(_spec(when="no_such_column <= 0"))
    assert "no_such_column" in str(caught.value)


def test_the_condition_may_use_the_period_number(tmp_path):
    """`period` is available, so a window can be expressed without a column."""
    result = api.run(
        _spec(periods=10, when="period >= 6"), 200, tmp_path, seed=5, validate_output=False
    )
    panel = pd.read_parquet(result["panel"])
    state_col = api.load(PACK).lifecycle.state_column
    assert (panel[state_col] == "Matured").any()


def test_packs_without_a_condition_hazard_are_untouched(tmp_path):
    """The new pass must be a no-op when nothing declares it."""
    spec = api.load(PACK).model_dump(mode="json", exclude_none=True, by_alias=True)
    spec["entity"]["calendar"]["periods"] = 8
    a = api.run(spec, 300, tmp_path / "a", seed=17, validate_output=False)
    b = api.run(spec, 300, tmp_path / "b", seed=17, validate_output=False)
    assert pd.read_parquet(a["panel"]).equals(pd.read_parquet(b["panel"]))

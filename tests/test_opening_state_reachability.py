"""A state every entity starts in is reachable, even if nothing transitions into it.

Whole families of lending products begin in a phase they never return to:

    interest-only  -> repayment        (mortgages, CRE)
    deferment      -> repayment        (student loans)
    promotional    -> standard rate    (cards, BNPL)
    revolving      -> amortising       (many ABS structures)

The reachability check counted transition-matrix states and hazard targets, and
not the opening distribution — so it called the phase every loan begins in dead
and refused the spec outright. Nothing could express a product with a first
phase.
"""

from __future__ import annotations

import pytest

from sdd import api
from sdd.spec import SpecError

PACK = "clo_eu_leveraged_loans"


def _with_opening_phase(*, reachable_by_hazard: bool) -> dict:
    """Add a phase every facility starts in and leaves after six periods."""
    spec = api.load(PACK).model_dump(mode="json", exclude_none=True, by_alias=True)
    lc = spec["lifecycle"]
    lc["states"] = ["Ramp", *lc["states"]]
    lc["transition_states"] = [s for s in lc["states"] if s not in lc["terminal"] and s != "Ramp"]
    lc["initial_distribution"] = {"Ramp": 1.0}
    if reachable_by_hazard:
        lc["hazards"] = [
            *lc["hazards"],
            {
                "kind": "condition",
                "name": "ramp_ends",
                "when": "period >= 6",
                "to_state": "Performing",
                "from_states": ["Ramp"],
            },
        ]
    for column in spec["columns"]:
        if column["name"] == lc["state_column"]:
            column["domain"] = ["Ramp", *column["domain"]]
    return spec


def test_a_state_only_the_opening_distribution_reaches_is_accepted():
    result = api.check(_with_opening_phase(reachable_by_hazard=True))
    assert result["valid"], result["problems"]


def test_a_state_nothing_reaches_at_all_is_still_refused():
    """The check must still catch a genuinely dead state.

    Without this the fix would have widened into 'anything named in the
    lifecycle is fine', which is the opposite of useful.
    """
    spec = api.load(PACK).model_dump(mode="json", exclude_none=True, by_alias=True)
    lc = spec["lifecycle"]
    lc["states"] = [*lc["states"], "Orphan"]
    for column in spec["columns"]:
        if column["name"] == lc["state_column"]:
            column["domain"] = [*column["domain"], "Orphan"]

    with pytest.raises(SpecError, match="Orphan"):
        api.load(spec)


def test_a_zero_weighted_opening_state_does_not_count():
    """Listed at zero probability, no entity ever starts there."""
    spec = _with_opening_phase(reachable_by_hazard=True)
    lc = spec["lifecycle"]
    lc["initial_distribution"] = {"Ramp": 0.0, "Performing": 1.0}
    lc["hazards"] = [h for h in lc["hazards"] if h.get("name") != "ramp_ends"]

    result = api.check(spec)
    assert not result["valid"]
    assert any("Ramp" in p for p in result["problems"])


def test_the_shipped_packs_are_unaffected():
    for pack in api.list_packs():
        assert api.check(pack)["valid"]

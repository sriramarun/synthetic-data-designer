"""Shared fixtures.

``minimal_spec_dict`` is deliberately small and asset-class-neutral: two states,
one hazard, one amortisation kernel. Tests that want to break something start
from it and mutate one field, which keeps each negative control readable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKS = REPO_ROOT / "packs"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Where the §31 release suite runs.

    Registered here rather than in `tests/release/conftest.py` because pytest
    reads `pytest_addoption` only from the rootdir plugin — declared in a
    subdirectory it is silently ignored, and the flag comes back as an
    unrecognised argument.
    """
    parser.addoption(
        "--release-target",
        action="store",
        default="local",
        help=(
            "'local' (default) runs the release suite in this process; a URL runs the "
            "same tests against that deployed instance, e.g. "
            "--release-target=https://example.hf.space"
        ),
    )


@pytest.fixture
def minimal_spec_dict() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "meta": {"name": "mini", "asset_class": "test"},
        "entity": {
            "id_column": "loan_id",
            "time_column": "as_of",
            "calendar": {"start": "2024-01-31", "periods": 4, "freq": "month_end"},
        },
        "params": {"deal_year": 2024},
        "constants": {"currency": "EUR"},
        "columns": [
            {
                "name": "loan_id",
                "role": "static",
                "dtype": "str",
                "generator": {"kind": "sequence", "prefix": "L", "width": 4},
            },
            {
                "name": "as_of",
                "role": "dynamic",
                "dtype": "str",
                "generator": {"kind": "constant", "value": "2024-01-31"},
            },
            {"name": "currency", "role": "constant"},
            {
                "name": "state",
                "role": "dynamic",
                "dtype": "category",
                "generator": {
                    "kind": "categorical",
                    "values": ["Performing", "Late"],
                    "weights": [0.9, 0.1],
                },
            },
            {
                "name": "balance",
                "role": "dynamic",
                "dtype": "float",
                "generator": {"kind": "uniform", "low": 100000, "high": 200000, "decimals": 2},
            },
            {
                "name": "rate",
                "role": "static",
                "dtype": "float",
                "generator": {"kind": "constant", "value": 3.0},
            },
            {
                "name": "payment",
                "role": "static",
                "dtype": "float",
                "generator": {"kind": "constant", "value": 900.0},
            },
            {
                "name": "term",
                "role": "dynamic",
                "dtype": "int",
                "generator": {"kind": "constant", "value": 300},
            },
            {"name": "arrears", "role": "derived", "dtype": "float"},
            {"name": "band", "role": "derived"},
        ],
        "derivations": [
            {"target": "arrears", "expr": "0.0"},
            {
                "target": "band",
                "kind": "bucket",
                "bucket": "b",
                "source": "balance",
                "stage": "both",
            },
        ],
        "buckets": {"b": {"bins": [0, 150000, 1000000], "labels": ["low", "high"]}},
        "lifecycle": {
            "state_column": "state",
            "states": ["Performing", "Late", "Redeemed"],
            "terminal": ["Redeemed"],
            "transitions": [[0.95, 0.05], [0.50, 0.50]],
            "hazards": [
                {
                    "kind": "bernoulli",
                    "name": "prepayment",
                    "annual_rate": 0.12,
                    "to_state": "Redeemed",
                }
            ],
            "state_fields": {"Redeemed": {"balance": 0.0}},
        },
        "dynamics": {
            "amortisation": {
                "kind": "annuity",
                "balance": "balance",
                "rate": "rate",
                "payment": "payment",
                "only_when_state": "Performing",
            },
            "counters": [{"column": "term", "step": -1, "clip_min": 0}],
            "accruals": [{"column": "arrears", "add": "payment", "when": "not_performing"}],
        },
        "emit": {
            "filename": "mini_{yyyymm}.csv",
            "column_order": ["loan_id", "as_of", "currency", "state", "balance", "arrears", "band"],
        },
    }


@pytest.fixture
def rmbs_spec_path() -> Path:
    return PACKS / "rmbs_nl_green_lion.yaml"

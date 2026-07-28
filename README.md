# synthetic-data-designer

**Give it a structure and some sample data. It works out how the data behaves, then
generates a synthetic portfolio and ages it forward in time.**

A spec-driven generator for structured-finance loan tapes. Point it at any asset
class — residential mortgages, auto loans, SME facilities, CRE — and it produces a
coherent **monthly panel**: the same loans observed month after month, paying down,
falling behind, prepaying, defaulting.

Generalised from
[`Algoritmica-ai/deeploans/synthetic-data-designer`](https://github.com/Algoritmica-ai/deeploans/tree/main/synthetic-data-designer),
which did this for one Dutch RMBS deal with every fact hardcoded in Python. Here
every one of those facts lives in an editable spec file, and a profiler can write
that spec for you by reading your data.

> **Status: work in progress.** The spec format and generation engine are landing
> first; see [Milestones](#milestones) for what is and is not built yet.

---

## The idea in one picture

```
  structure                          ┌─> profile.json   what the data looks like
  (ESMA taxonomy / CSV header) ──┐   │
                                 ├───┤
  sample data                    │   └─> spec.yaml      editable knobs
  (a real or reference tape) ────┘            │
                                              │
        requirements ──────────────────┐      │
        (rows, periods, scenario)      ▼      ▼
                               ┌──────────────────────┐
                               │   generate  +  age   │
                               └──────────┬───────────┘
                                          ▼
                      per-period tapes  +  panel.parquet
                      +  invariant report  +  fidelity report
```

---

## Vocabulary

If you don't work in securitisation daily, these five terms carry the whole design:

| Term | Plain meaning |
|---|---|
| **Loan tape** | One CSV, one row per loan, as at a single date. Also called a *cut-off*. |
| **Panel** | Many tapes stacked — the same loans, month after month. |
| **Ageing** | Walking each loan forward: it pays down, falls behind, prepays, defaults. |
| **Transition matrix** | A table of "if a loan is Performing this month, what's the chance it's 30 days late next month?" One row per state, each row summing to 1. |
| **Hazard** | A per-month chance of an event — e.g. 0.6%/month of paying off early. |

---

## Why a spec

Upstream hardcoded five layers to one deal. This project moves all five into data:

| Layer | Upstream | Here |
|---|---|---|
| Schema | 71 column names in a Python list | `columns:` in the spec |
| Generation | Dutch province weights, €300k balances | `generator:` per column |
| Ageing | A 6×6 mortgage delinquency matrix | `lifecycle.transitions:` |
| Output | `green_lion_<yyyymm>_*.csv` | `emit.filename:` |
| Tests | 53 SQL assertions naming those columns | checks generated from the spec |

The spec is validated by pydantic and cross-checked on load, so a typo, an unknown
column reference, a transition row summing to 1.1, or a self-referential formula is
an error *before* a long run starts rather than twenty minutes into it.

Formulas in a spec are evaluated by a restricted syntax-tree walker — arithmetic,
comparisons, boolean logic, conditionals, and a fixed function whitelist. There is
no `eval`, no attribute access, no imports. **A spec is data, never code**, which
matters because specs are uploaded files.

---

## Quick look at a spec

```yaml
spec_version: 1
meta:   {name: nl_rmbs, asset_class: rmbs, regulatory_template: ESMA Annex 2}
entity:
  id_column: loan_id
  id_format: "GL{deal_year}_{seq:06d}"
  time_column: reporting_date
  calendar: {start: 2024-01-31, periods: 24, freq: month_end}

constants: {currency: EUR, country: NL, originator_name: ING}

columns:
  - name: province
    role: static
    generator: {kind: categorical, values: [Zuid-Holland, ...], weights: [0.230, ...]}
  - name: original_balance
    role: static
    generator: {kind: scipy, dist: lognorm, params: {s: 0.40, scale: 300000}}

derivations:
  - target: original_market_value_at_origination
    expr: "original_balance / (oltomv_original / 100)"
    round: 2

lifecycle:
  states: [Performing, 1-29 DPD, 30-59 DPD, 60-89 DPD, 90+ DPD, Defaulted, Charged-Off, Redeemed]
  state_column: arrears_bucket
  transitions: [[0.9925, 0.0060, ...], ...]
  absorbing: [Defaulted]          # stuck, but still in the pool
  terminal:  [Charged-Off, Redeemed]   # written once, then leaves the pool
  hazards:
    - {kind: bernoulli,  name: prepayment, annual_rate: 0.07, to_state: Redeemed}
    - {kind: dwell_time, name: chargeoff,  from_state: Defaulted, periods: 9, to_state: Charged-Off}

dynamics:
  amortisation: {kind: annuity, balance: current_balance, rate: current_interest_rate_pct,
                 payment: scheduled_monthly_payment, only_when_state: Performing}
  indices: [{name: hpi, kind: constant_drift, annual: 0.03,
             applies_to: [indexed_market_value]}]
```

Amortisation kinds cover non-mortgage assets without touching the engine:
`annuity`, `linear`, `bullet`, `interest_only`, `revolving` (credit cards),
`depreciation` (auto residual values).

---

## Install

```bash
pip install -e '.[dev]'
```

Core dependencies are light: numpy, pandas, pyarrow, scipy, pydantic, pyyaml,
duckdb, typer. Two optional extras:

```bash
pip install -e '.[nemo]'   # sample via NVIDIA NeMo Data Designer instead of numpy
pip install -e '.[deep]'   # CTGAN/SDV polish step on top of the rule-based sample
```

Neither is required. The numpy backend is the default and runs anywhere.

---

## Milestones

- [x] **M0** — scaffold, packaging, CI
- [ ] **M1** — spec schema, loader, samplers, deriver, RMBS pack
- [ ] **M2** — generalised ageing engine
- [ ] **M3** — invariant + fidelity validation
- [ ] **M4** — profiler: sample data → spec
- [ ] **M5** — optional NeMo and CTGAN backends
- [ ] **M6** — JSON API façade (the seam a UI plugs into)

A web UI is deliberately a later phase; M6 exists so it becomes a thin layer over
an engine that is already proven from the CLI.

---

## Licence

Apache 2.0, matching upstream deeploans. See [`NOTICE`](NOTICE) for attribution of
the residential-mortgage domain calibration.

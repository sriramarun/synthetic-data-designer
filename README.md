# synthetic-data-designer

**Give it a structure and some sample data. It works out how the data behaves, then
generates a synthetic portfolio and ages it forward in time.**

A spec-driven generator for structured-finance loan tapes. Point it at any asset
class — residential mortgages, auto leases, SME facilities, CRE — and it produces a
coherent **panel**: the same loans observed period after period, paying down,
falling behind, prepaying, defaulting.

Generalised from
[`Algoritmica-ai/deeploans/synthetic-data-designer`](https://github.com/Algoritmica-ai/deeploans/tree/main/synthetic-data-designer),
which did this for one Dutch RMBS deal with every fact hardcoded in Python. Here
every one of those facts lives in an editable spec file, and a profiler can write
that spec for you by reading your data.

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
                      +  run manifest (hash, seed, versions)
```

---

## Try it

```bash
pip install -e '.[dev]'
```

```bash
sdd packs
```

```bash
sdd run rmbs_nl_green_lion -n 50000 -o ./out
```

That generates 50,000 Dutch mortgages, ages them across 24 monthly cut-offs, writes
24 CSVs plus a consolidated panel, and checks 22 invariants against the result.

Now analyse what it produced, as if it were someone else's data:

```bash
sdd design ./out/all_cutoffs.parquet -o relearned.yaml
```

```bash
sdd run relearned.yaml -n 50000 -o ./out2
```

Stress it:

```bash
sdd run rmbs_nl_green_lion -n 50000 -o ./severe --scenario severe
```

| scenario | distress at month 24 | mean collateral value |
|---|---|---|
| `base` | 1.7% | €557,577 |
| `adverse` | 7.8% | €481,353 |
| `severe` | 23.1% | €386,891 |

*(6,000 loans, seed 42, 24 monthly periods. Invariants pass in all three.)*

---

## Vocabulary

If you don't work in securitisation daily, these five terms carry the whole design:

| Term | Plain meaning |
|---|---|
| **Loan tape** | One CSV, one row per loan, as at a single date. Also called a *cut-off*. |
| **Panel** | Many tapes stacked — the same loans, period after period. |
| **Ageing** | Walking each loan forward: it pays down, falls behind, prepays, defaults. |
| **Transition matrix** | A table of "if a loan is Performing this month, what's the chance it's 30 days late next month?" One row per state, each row summing to 1. |
| **Hazard** | A per-period chance of an event — e.g. 0.6%/month of paying off early. |

---

## Why a spec

Upstream hardcoded five layers to one deal. This project moves all five into data:

| Layer | Upstream | Here |
|---|---|---|
| Schema | 71 column names in a Python list | `columns:` |
| Generation | Dutch province weights, €300k balances | `generator:` per column |
| Ageing | A 6×6 mortgage delinquency matrix | `lifecycle.transitions:` |
| Output | `green_lion_<yyyymm>_*.csv` | `emit.filename:` |
| Tests | 53 SQL assertions naming those columns | checks generated from the spec |

The spec is validated by pydantic and cross-checked on load, so a typo, an unknown
column reference, a transition row summing to 1.1, a self-referential formula, or an
unreachable state is an error *before* a long run starts.

Formulas are evaluated by a restricted syntax-tree walker — arithmetic, comparisons,
boolean logic, conditionals, and a fixed function whitelist. No `eval`, no attribute
access, no imports. **A spec is data, never code**, which matters because specs are
uploaded files.

Amortisation kinds cover non-mortgage assets without touching the engine:
`annuity`, `linear`, `bullet`, `interest_only`, `revolving` (credit cards),
`depreciation` (auto residual values). Calendars can be monthly, quarterly or annual;
hazards quoted annually convert to whichever you choose.

---

## What the profiler recovers

Pointed at a panel it has never seen, with no pack:

| | recovered |
|---|---|
| entity and cut-off columns | by name, else by behaviour, with the reason reported |
| static / dynamic / constant | by counting distinct values per entity |
| distributions | best of lognormal, normal, gamma, exponential, uniform, beta by KS; empirical fallback |
| lifecycle | states ordered best-first, transition matrix counted from observed moves |
| terminal vs absorbing states | by behaviour — who stops being reported, who never leaves |
| amortisation kind | by testing observed balance paths against each kernel |
| counters | columns moving by a fixed step each period |
| index drift | geometric mean growth of valuation columns |
| bucket columns | recovered as derivations, verified by re-deriving every label |

On the RMBS round trip it recovers the state ordering, both terminal states, the
absorbing state, annuity-only-when-performing, all four counters, six bucket columns,
and a transition matrix within 0.005 of the hand-set one. The regenerated panel
passes 39/39 of its own invariants.

### What it does not recover

**Relationships between columns.** Marginal profiling fits each column
independently, so a loan-to-value sampled beside a balance and a valuation will not
equal their ratio. Measured on the round trip: marginals come back within a couple of
percentage points, the largest pairwise correlation is off by ~1.0.

Three ways to close that gap, in increasing cost:

1. Write the relationship as a `derivation` in the spec. Cheap, exact, auditable.
2. Let the profiler recover it — bucket columns already are; ratios are not yet.
3. Turn on the optional CTGAN/TVAE polish (`pip install 'sdd[deep]'`) against a
   real seed dataset. The fidelity report shows before and after, so the step has
   to earn its keep.

---

## Validation

Two layers, answering different questions.

**Invariants** — is it internally consistent? Generated from the spec, so declaring
a column static produces a check that it never changes. Run over the panel with
DuckDB. Every check has a negative-control test proving it can fail.

**Fidelity** — does it look like the real thing? KS per numeric column, total
variation per categorical, correlation delta for joint structure, transition delta
for dynamics.

Thresholds sit at a measured **noise floor** rather than a flat number, because TV
noise grows with cardinality: at 20k rows a 40-category column lands 0.02 apart by
chance while a 2-category flag lands under 0.005. The multiple was calibrated over
868 same-generator comparisons, not guessed.

What it can and cannot detect at 20k rows, stated plainly:

- catches a 3% shift in a balance distribution, a 5% shift in a category's share,
  a 0.2pp rate shift, and a decoupled income column;
- **cannot** catch a 2% shift spread across a 12-category column — that is genuinely
  inside sampling noise, and needs more rows rather than a looser threshold.

---

## Install

```bash
pip install -e '.[dev]'
```

Core dependencies are light: numpy, pandas, pyarrow, scipy, pydantic, pyyaml,
duckdb, typer. Two optional extras, neither required:

```bash
pip install -e '.[nemo]'   # sample via NVIDIA NeMo Data Designer instead of numpy
pip install -e '.[deep]'   # CTGAN/TVAE polish on top of the rule-based sample
```

---

## Commands

```
sdd packs                             list the bundled asset-class packs
sdd profile SAMPLE                    analyse a tape, print what it found
sdd design SAMPLE -o spec.yaml        analyse it and write a runnable spec
sdd check SPEC                        validate a spec without running it
sdd run SPEC -n 50000 -o ./out        generate and age
sdd validate SPEC PANEL               check a panel against its spec
sdd fidelity REFERENCE SYNTHETIC      score synthetic against real
```

Every command is a thin wrapper over `sdd.api`, which takes and returns plain JSON.
That is the seam a web UI plugs into — the CLI is simply its first consumer.

---

## Status

| | |
|---|---|
| **M0** scaffold, packaging, CI | done |
| **M1** spec schema, loader, samplers, deriver, RMBS pack | done |
| **M2** generalised ageing engine | done |
| **M3** invariant + fidelity validation | done |
| **M4** profiler: sample data → spec | done |
| **M5** optional NeMo and CTGAN backends | done |
| **M6** JSON API façade + CLI | done |
| **UI** | not started — deliberately a later phase |

246 tests. `pytest` green and `ruff` clean on every commit.

Honest scope notes:

- One calibrated pack ships (Dutch RMBS). Auto, CRE, SME and consumer packs are not
  written yet, though the engine runs them — `tests/test_cross_asset_class.py`
  exercises a quarterly depreciating auto lease end to end.
- The optional NeMo and CTGAN paths are implemented and their failure modes tested,
  but neither extra is installed in CI, so they are not exercised against a live
  backend here.
- No cash-flow waterfall or tranche modelling. This produces collateral, not bonds.

---

## Licence

Apache 2.0, matching upstream deeploans. See [`NOTICE`](NOTICE) for attribution of
the residential-mortgage domain calibration and the ESMA taxonomy fixture.

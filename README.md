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
  schema                            ┌──> profile.json   what the data looks like
  (taxonomy / header / dictionary)  │
  CSV · Parquet · Excel · JSON  ────┤
                                    │
  sample data                       └──> spec.yaml      every editable knob
  (a real or reference tape)  ──────┘         │
                                              │
        requirements ──────────────────┐      │
        (rows, periods, method,        │      │
         rates, randomness)            ▼      ▼
                               ┌──────────────────────┐
                               │   generate  +  age   │
                               └──────────┬───────────┘
                                          ▼
                      per-period tapes  +  panel.parquet
                      +  invariant report  +  fidelity report
                      +  run manifest (hash, seed, versions)
                      +  CSV / Parquet / Excel / YAML / HTML report
```

---

## Try it

```bash
pip install -e '.[dev,web]'
```

```bash
sdd ui
```

A local page at `http://127.0.0.1:8000` walks the whole flow, in six steps. Nothing
leaves the machine — files are read, profiled and deleted with the workspace.

```
Upload → Review → Configure → Generate → Results → Download
```

**1. Upload.** A schema (CSV, Parquet, Excel or JSON — a header, a parquet schema, a
data dictionary, or an ESMA taxonomy) and, optionally, sample data. Either alone is
enough: a schema fixes the columns, a sample measures the distributions, and
together the schema wins on structure while the sample wins on shape. Or load a
bundled pack and skip ahead.

**2. Review schema.** Every column with its detected type, role, primary key, date
columns, nullability and the confidence behind each inference. Names, types and
required/optional are editable here. A rename travels: the entity, the emit order,
the lifecycle, the dynamics and every expression referring to it are rewritten too.

**3. Configure.** Six tabs, all editing one document:

| Tab | What it holds |
|---|---|
| Scale | rows, seed, stress scenario |
| Generation method | statistical · distribution based · rule based · sampling · CTGAN · hybrid |
| Randomness | noise, correlation, outliers, missing values |
| Data aging | periods, monthly/quarterly/annual, default rate, prepayment rate, recovery rate, new loans per period, and the transition matrix as a live grid |
| Schema | per-column distribution parameters |
| Advanced | the YAML itself, which updates as you change anything above |

**New loans.** Left at zero, the pool is closed: every loan exists at the first
cut-off and the pool only shrinks. Above zero it stays open, so a run starting in
December 2024 and ageing 24 months holds loans written across 2025 and 2026 as
well as the opening book. They are drawn from the same distributions, dated to the
cut-off they arrive at, and aged from there.

**4. Generate.** Seven named stages, a progress bar and an estimated finish. The run
happens on a worker thread, so nothing depends on the browser staying open.

**5. Results.** Rows, columns, time taken, validation verdict; distribution
comparison against the sample, delinquency curve, LTV distribution and pool balance
over time; and the generated data in a table you can search, sort and page. All four
charts and the table are computed server-side against the file, so a
twelve-million-row panel behaves like a twelve-thousand-row one.

**6. Download.** CSV, Parquet, Excel, the configuration as YAML, and a standalone
validation report.

Every edit is validated by the same loader the CLI uses, so the UI cannot accept a
spec the engine would reject — and every control writes into the configuration, so
whatever the forms do, the YAML tab shows.

**[→ The user guide](docs/USER-GUIDE.md)** walks every screen and every setting,
including the ones only the YAML exposes. **[→ Hosting it](docs/DEPLOYMENT.md)**
covers Hugging Face Spaces and what changes when the instance is shared.

Or drive it from the command line:

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

## The calibrated packs

Three ship, deliberately unlike each other — a pack is only worth having if it
exercises the engine differently.

| | `clo_eu_leveraged_loans` | `rmbs_nl_green_lion` | `auto_abs_esma_annex5` |
|---|---|---|---|
| **Shown as** | European CLO — Leveraged Loans | Dutch Green Loans — Residential Mortgages | European Auto Loans — ESMA Annex 5 |
| Template | none — portfolio analytics | ESMA Annex 2, residential real estate | ESMA Annex 5, automobile |
| Columns | 56 | 71 | 44 |
| Borrower | a **company**, several loans each | a household, one loan | a household, one contract |
| Collateral | none — senior secured claim | a house, **indexed upward** | a car, **depreciating** at 15%/yr |
| Balance | **bullet** — flat, then repaid at maturity | annuity or interest-only | annuity, with a balloon on PCP |
| Pool | **open** — buys new loans for 24 months | closed | closed |
| Ladder | watchlist → distressed → defaulted | 1-29 → 30-59 → 60-89 → 90+ DPD | 1-30 → 31-60 → 61-90 DPD |
| Exits | prepaid, sold, **matured**, recovered | prepaid, charged off | prepaid, charged off |
| Default → resolution | 9-month workout, then **recovery at 62%** | 9 months to write-off | 6 months, then recovery at 45% |
| Extras | **credit ratings migrate on their own chain**; 19 monthly portfolio metrics | — | — |
| Calibrated to | demo assumptions, directional only | upstream deeploans' Dutch RMBS deal | published European prime auto ABS ranges |


### European CLO — Leveraged Loans

The newest pack and the one that stretched the engine most. If you have not met
a CLO, the vocabulary is worth five minutes.

**What it models.** A **CLO** is a fund that buys several hundred loans made to
companies — not to people — and sells shares in the bundle to investors. This
pack generates *the loans the fund owns*, month by month. It does **not** model
the fund's own liabilities: no tranches, no waterfall, no OC or IC tests, no
equity returns. Those are a separate problem and a separate product.

**Facility and obligor.** A **facility** is one loan. An **obligor** is the
company that borrowed it. One company usually has several facilities, and every
limit investors care about is stated per *obligor* — which is why the pack groups
them, and why the industry, country and revenue of a company are shared by all
its loans rather than drawn separately for each.

**An open pool.** A mortgage pool is sealed: loans only leave. A CLO manager
actively buys and sells for a few years — the **reinvestment period** — and then
stops. New facilities join every month until month 24 here, and after that the
portfolio only runs down.

**Credit migration.** A corporate loan does not go 30, then 60, then 90 days past
due. It goes on a **watchlist**, becomes **distressed**, and then **defaults**.
Default is not the end: a **workout** runs for nine months, the facility stays in
the portfolio throughout, and some of the money comes back.

**Ratings move on their own.** A rating is an opinion about whether a borrower
*can* pay, not a record of whether it *has*, so it drifts while a facility is
performing perfectly — and it usually moves first. Measured on this pack, a
downgrade precedes visible distress about 80% of the time, with a median five
months of warning. That matters because every CLO indenture caps how much
CCC-rated collateral the fund may hold, and a rating derived from the credit
state could only ever confirm what was already obvious.

**Turnover.** Facilities leave four ways: repaid early (**prepaid**), sold by the
manager, reaching the end of their term (**matured**), or resolved out of default
(**recovered**). Each is modelled separately because each means something
different to an investor.

**What you get.** 500 facilities over 36 monthly cut-offs: one loan tape per
month, a consolidated panel, a 19-figure portfolio report per cut-off, four
CLO-specific charts, 45 invariants checked, and three stress scenarios.

**What it is not.** Demo calibration. The transition probabilities, recovery
rates and spreads are plausible and internally consistent; they are not estimates
of any real market, manager or deal, and the pack must not be described as
satisfying a particular indenture or rating-agency methodology.

```bash
sdd run clo_eu_leveraged_loans -n 500 -o ./clo --scenario adverse
```

```bash
sdd run auto_abs_esma_annex5 -n 20000 -o ./auto --scenario severe
```

On the ESMA side, be precise about what you are getting: the column names follow
the Annex 5 field vocabulary and the section ordering of the disclosure template,
and the format hints map onto the types. That is the **shape** a filing takes, and
it is not a filing — check the field codes and the full field list against the
official ESMA XML schema before submitting anything derived from it. What the pack
gives you is realistic test data in the right structure.

---

## Vocabulary

If you don't work in securitisation daily, these six terms carry the whole design:

| Term | Plain meaning |
|---|---|
| **Loan tape** | One CSV, one row per loan, as at a single date. Also called a *cut-off*. |
| **Panel** | Many tapes stacked — the same loans, period after period. |
| **Ageing** | Walking each loan forward: it pays down, falls behind, prepays, defaults. |
| **Transition matrix** | A table of "if a loan is Performing this month, what's the chance it's 30 days late next month?" One row per state, each row summing to 1. |
| **Hazard** | A per-period chance of an event — e.g. 0.6%/month of paying off early. |
| **Closed vs open pool** | A closed pool holds every loan at the first cut-off and only shrinks. An open one keeps lending, so loans written during the window appear at the cut-off they were written on. |

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
| correlation | Spearman rank correlation between numeric columns, reimposed by reordering |
| open vs closed pool | entities first seen after the opening cut-off, as a rate; and whether they arrived newly written or acquired seasoned, read off their own counters |

On the RMBS round trip it recovers the state ordering, both terminal states, the
absorbing state, annuity-only-when-performing, all four counters, six bucket columns,
and a transition matrix within 0.005 of the hand-set one. The regenerated panel
passes 39/39 of its own invariants.

### How columns are made to move together

Fitting each column on its own produces a book where every column is individually
right and jointly wrong: a loan-to-value sampled beside a balance and a valuation
does not equal their ratio, and nothing ties income to loan size. Three mechanisms
close that gap, in increasing cost:

1. **Derivations.** Write the relationship into the spec and it holds exactly, every
   period. Cheap, auditable, and how every ratio and band in the RMBS pack works.
   Bucket columns are recovered as derivations automatically.
2. **Rank correlation.** The profiler measures the Spearman correlation between
   numeric columns and stores it in the spec; the randomness stage reorders the
   generated values to match it. Reordering cannot change a column's own
   distribution, so the fitted marginals survive untouched while the joint structure
   appears. The **Correlation** control scales it continuously, 0 to 1. On the RMBS
   round trip this takes the largest pairwise correlation error from **~1.0 to
   0.08**.
3. **A deep model.** The optional CTGAN/TVAE polish (`pip install 'sdd[deep]'`)
   learns the joint distribution from a real seed dataset. Slow, needs the real
   tape, and not auditable — so the fidelity report shows before and after, and the
   step has to earn its keep.

Rank correlation is monotonic by construction: it reproduces "bigger loans go with
bigger incomes", not a curved or conditional relationship. Those still need a
derivation or the deep model.

---

## Parity with upstream

`scripts/parity_check.py` runs upstream deeploans and this engine side by side
and compares them. Measured at 30,000 loans x 24 cut-offs:

| bar | result |
|---|---|
| schema | **exact** — 71 columns, identical order, identical filenames |
| behaviour | **exact** — both panels pass all 22 invariants; surviving pool within 0.22% at every cut-off |
| joint structure | max correlation delta 0.025 (floor 0.05) |
| dynamics | max transition delta 0.028 (floor 0.053) |
| marginals | 66/69 within their noise floor |

```bash
pip install data-designer
python scripts/parity_check.py --deeploans /path/to/deeploans -n 30000
```

Byte-identical output is **not** achievable and is not claimed: NeMo Data
Designer and numpy do not share a random number stream. The three columns that
sit marginally over their floor are not the same three from run to run. Chased
to the bottom, the most persistent one traces to the underlying Bernoulli draw
landing 1.7 standard errors apart on the opening book — ordinary sampling noise,
amplified at panel level because a loan surviving 24 cut-offs contributes 24 rows
while one redeeming early contributes two. Demanding zero would mean tuning
thresholds until one seed passed.

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
duckdb, typer, openpyxl. Three optional extras:

```bash
pip install -e '.[web]'    # the six-step web UI
pip install -e '.[nemo]'   # sample via NVIDIA NeMo Data Designer instead of numpy
pip install -e '.[deep]'   # CTGAN/TVAE polish, and the CTGAN/hybrid methods in the UI
```

---

## Commands

```
sdd ui                                open the local web UI
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
| **M7** six-step web UI: upload → review → configure → generate → results → download | done |
| **M8** open pools: new loans written during the ageing window | done |

423 tests. `pytest` green and `ruff` clean on every commit.

Honest scope notes:

- Three calibrated packs ship: European CLO leveraged loans, Dutch residential
  mortgages and European auto loans.
  CRE, SME and consumer packs are not written yet, though the engine runs them —
  `tests/test_cross_asset_class.py` exercises a quarterly depreciating auto lease
  end to end.
- The auto pack is calibrated to *published ranges* for the asset class, not to any
  one deal's tape. Its ESMA alignment is structural — field vocabulary, section
  order, format hints — and is not a substitute for validating against the official
  schema before a filing.
- The UI has no login, no projects and no stored history: one wizard, from upload to
  download, with the workspace on disk beside it. Accounts, saved projects and
  version history layer on top of this flow without changing it.
- The optional NeMo and CTGAN paths are implemented and their failure modes tested,
  but neither extra is installed in CI, so they are not exercised against a live
  backend here.
- No cash-flow waterfall or tranche modelling. This produces collateral, not bonds.

---

## Licence

Apache 2.0, matching upstream deeploans. See [`NOTICE`](NOTICE) for attribution of
the residential-mortgage domain calibration and the ESMA taxonomy fixture.

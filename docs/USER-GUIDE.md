# User guide

Every screen, every control, and every setting that exists — including the ones
the forms do not show, which live in the Advanced tab's YAML.

Start the app:

```bash
sdd ui
```

It serves on <http://127.0.0.1:8000>, binds to localhost only, and writes
everything it produces to a `.sdd-workspace` folder in the directory you launched
it from. Nothing is uploaded anywhere.

**Contents**

- [How the whole thing fits together](#how-the-whole-thing-fits-together)
- [Step 1 — Upload](#step-1--upload)
- [Step 2 — Review schema](#step-2--review-schema)
- [Step 3 — Configure](#step-3--configure)
  - [Scale](#tab-1-scale) · [Generation method](#tab-2-generation-method) ·
    [Randomness](#tab-3-randomness) · [Data aging](#tab-4-data-aging) ·
    [Schema](#tab-5-schema) · [Advanced](#tab-6-advanced)
- [Step 4 — Generate](#step-4--generate)
- [Step 5 — Results](#step-5--results)
- [Step 6 — Download](#step-6--download)
- [Everything only the YAML exposes](#everything-only-the-yaml-exposes)
- [The command line](#the-command-line)
- [Troubleshooting](#troubleshooting)

---

## How the whole thing fits together

One idea explains the entire interface: **every control writes into a single
YAML document, and that document is what runs.** The forms are a friendly way to
edit it, the Advanced tab shows it, and the Download step hands it to you. There
is no hidden state — a saved configuration plus a seed reproduces a dataset
exactly.

```
Upload  →  Review  →  Configure  →  Generate  →  Results  →  Download
  │           │           │            │            │           │
  schema    columns    the YAML     the engine   charts +    5 formats
  + sample  + types    document                  data table
```

You can move backwards at any time using the rail on the left. Steps unlock as
you complete them.

---

## Step 1 — Upload

Two boxes and a shortcut. You need **at least one** of the two boxes; the
shortcut skips both.

### Schema (marked Required)

The columns your output must have.

| Accepted | What is read from it |
|---|---|
| `.csv` / `.tsv` | The header row — or, if the file looks like a **data dictionary**, one row per field |
| `.xlsx` / `.xlsm` / `.xls` | Same, from the first sheet |
| `.parquet` / `.pq` | The parquet schema: column names with their physical types |
| `.json` | An **ESMA/ECB taxonomy** (`field_name` + `format_hint`), or plain row objects whose keys are the columns |

A **data dictionary** is detected when the table has a name-ish column (`name`,
`field`, `field_name`, `column`, `column_name`) *and* something describing each
field (`type`, `dtype`, `data_type`, `format`, `format_hint`, `description`,
`content_to_report`, `comment`). Both markers are required — a tape read as a
dictionary would produce one field per row, and a dictionary read as a tape would
produce a two-column schema.

ESMA format hints are understood and become types: `{MONETARY}` → float,
`{PERCENTAGE}` → float bounded 0–100, `{DATEFORMAT}` → date, `{INTEGER}` → int,
`{ALPHANUM-28}` → string capped at 28 characters, `{Y/N}` → a category with
exactly two allowed values, `{NUTS}` / `{COUNTRYCODE}` / `{CURRENCYCODE}` →
categories.

### Sample data (Optional)

Real data to learn from — a loan tape, a transaction file, portfolio data.
Accepts everything the schema box does, plus `.jsonl`.

Uploading a sample changes what the tool can do, quite a lot:

| With a sample | Without |
|---|---|
| Every distribution is **measured** | Distributions are yours to choose |
| Static vs dynamic columns decided by counting | Everything assumed static |
| Lifecycle, transition matrix, attrition **learned** from repeated cut-offs | No lifecycle unless you write one |
| Correlation between columns measured and reproducible | Correlation control does nothing |
| **Sampling**, **CTGAN** and **Hybrid** methods unlocked | Only schema-only methods |
| Results show generated-vs-real comparison charts | Generated data only |

If your sample has the same entity at several cut-offs (the same `loan_id` in
January, February, March), it is a **panel** and behaviour over time can be
learned. A single snapshot still gives you every column's shape.

### Or start from a calibrated pack

| Pack | What it is |
|---|---|
| **Dutch Green Loans — Residential Mortgages** (`rmbs_nl_green_lion`) | 71 columns, ESMA Annex 2. Annuity mortgages, house-price index, 8-state delinquency ladder |
| **European Auto Loans — ESMA Annex 5** (`auto_abs_esma_annex5`) | 44 columns. Depreciating collateral, balloon payments on PCP contracts, fast write-off with recovery |

The grey monospace text is the pack's identifier, which is what `sdd run` takes
at a command line. Loading a pack jumps you to Review with everything already
configured.

### ➡ Analyze data

Reads the schema, profiles the sample, and builds a configuration. This is the
slow step on a large file — profiling reads up to 200,000 rows.

---

## Step 2 — Review schema

What was detected, and what you can change before anything is generated. A wrong
key or a mistyped column is cheap to fix here and expensive to find later.

### The summary tiles

| Tile | Meaning |
|---|---|
| **Columns** | Every declared column, including helpers |
| **Static** | Same value for an entity in every period (the region a property is in) |
| **Dynamic** | Changes over time (the outstanding balance) |
| **Derived** | Computed from other columns, never sampled — so it can never disagree with them |
| **Date columns** | Detected as dates, by type or by name |
| **Nullable** | Blank at least once in your sample. These are the columns the missing-values control may blank |
| **Need review** | Inferred with low confidence. Filter to these first |

Below the tiles, two notes name the **primary key** and the **date column**, each
with the reason it was chosen — matched by name, or by behaviour ("unique within
every cut-off and recurring across them").

### The column table

| Column | Editable | Notes |
|---|---|---|
| **Column** | ✅ rename | A rename travels: the entity, the emit order, the lifecycle, the dynamics and every expression referring to it are rewritten too |
| **Type** | ✅ `int` `float` `str` `category` `bool` `date` | |
| **Role** | — | static / dynamic / derived / constant / helper |
| **Key** | — | `key` marks the primary key, `date` a date column |
| **Required** | ✅ checkbox | Unticking makes the column **optional**, which is what the missing-values control in the next step is allowed to blank. Nothing else is ever blanked |
| **Distinct** | — | How many different values were seen |
| **Null in sample** | — | How often it was blank in your data |
| **Confidence** | — | How much to trust the inferred generator. Green ≥ 0.75, amber ≥ 0.5, red below |
| **Example** | — | Two real values from your sample |

**Filter columns…** narrows the table; **Needs review** shows only the low-confidence ones.

Edits are staged and applied when you press the button — the footer tells you how
many are pending.

### ➡ Generate configuration

Applies your edits, re-validates, and moves to Configure.

---

## Step 3 — Configure

Six tabs, all editing one document. Anything you change here appears in the
Advanced tab's YAML immediately.

The badge at the top right shows `valid · N cols · Np` or a problem count. You
cannot generate while it is red, and the problems are listed at the bottom of the
screen.

### Tab 1: Scale

| Control | Range | What it does |
|---|---|---|
| **Rows to generate** | ≥ 1 | Entities in the opening pool — loans, leases, accounts. Each appears once per period, so total rows ≈ this × periods |
| **Seed** | any integer | Same seed, same data, every time. Change it for a different draw of the same configuration |
| **Scenario** | from the spec | A named stress overlay. The packs ship `base`, `adverse`, `severe` |

A note below does the arithmetic: how many rows that adds up to, and a reminder
that loans leaving the pool make the real count a little lower.

### Tab 2: Generation method

How each column's values are drawn. Whatever you pick is written into the
configuration as generators you can read and edit.

| Method | What it does | Use it when | Needs |
|---|---|---|---|
| **Statistical** | Every numeric column becomes a normal with the same mean and spread | The level matters and the shape does not. Fast and obvious | — |
| **Distribution based** | The best-fitting named distribution per column — lognormal, gamma, beta — chosen by the profiler | Default. The most faithful closed-form option | — |
| **Rule based** | No fitted shape: numbers uniform inside their bounds, categories equally likely inside their domain | You have a schema and no data | — |
| **Sampling** | Resamples your observed values, spikes and all | Highest per-column fidelity; the only method that reproduces a zero-inflated column exactly | sample |
| **CTGAN** | A deep tabular model trained on your tape, learning how columns move *together* | Joint structure matters more than auditability | sample + `pip install 'sdd[deep]'` |
| **Hybrid** | Fitted distributions across the schema, then a deep polish over the columns it can improve | You want structure where it helps and a readable spec everywhere else | sample + `[deep]` |

Unavailable methods are greyed out and say why.

Two guarantees hold whichever you pick:

- **Identifiers, date columns and the lifecycle state column are never
  rewritten.** Turning a key into a normal distribution would destroy it.
- **A rewrite may narrow a range, never widen one.** Moment-matching a lognormal
  balance onto a normal would otherwise add a left tail the original never had,
  and a few per cent of your portfolio would come back negative. Columns are held
  inside what their original distribution allowed and what the spec asserts, and
  the notes tell you how many were bounded.

### Tab 3: Randomness

Applied after sampling and **before anything is derived** — so a ratio recomputed
from a jittered balance still matches that balance. Randomness never breaks
internal consistency.

| Control | Range | Step | What it does |
|---|---|---|---|
| **Noise** | 0 – 50% | 1% | Gaussian jitter on every numeric column, as a share of that column's own standard deviation. Zero-centred, so the mean holds and the spread grows. 5% is a realistic amount of measurement error |
| **Correlation** | 0 – 100% | 5% | How much of the correlation measured in your sample to reimpose. Columns are **reordered** to match, which changes how they move together without changing any column's own distribution. 0 leaves them independent, 100% matches the sample |
| **Outliers** | 0 – 10% | 0.5% | Share of rows pushed four standard deviations into the tail — the data-quality artefacts a downstream system should survive. Declared bounds are still respected |
| **Missing values** | 0 – 50% | 1% | Share of values blanked across **optional** columns only |

**Order matters and is fixed**: correlation → outliers → noise → missing values.
Outliers before noise, so an outlier is a decision rather than an accident of a
wide jitter; missing values last, so a blanked value is never first used to
compute something else.

**Never touched by any of them:** identifiers, the date column, the lifecycle
state column, anything a state pins a value to, amortisation inputs, counters and
accruals. Blanking those would not simulate messy data, it would simulate a
broken file.

If a control cannot do anything it says so rather than sitting there inert —
"every column is marked required", or "no sample was analysed, so no relationship
between columns was ever measured".

### Tab 4: Data aging

Walking the pool forward, period by period.

| Control | Range | What it does |
|---|---|---|
| **Number of periods** | ≥ 1 | How many cut-offs to emit, starting from the calendar's start date |
| **Frequency** | Monthly / Quarterly / Annually | How far apart the cut-offs are. The rates below are annual either way |
| **Lifecycle** | — | Read-only: the states this configuration has, in order |
| **Default rate** (annual) | 0 – 50% | Share of performing loans that default within a year. Setting it **rescales the transition matrix** and renormalises every row, so the matrix stays a valid matrix |
| **Prepayment rate** (annual) | 0 – 50% | Chance a healthy loan redeems early. Applied as the hazard rate the engine already uses |
| **Recovery rate** | 0 – 50% | Share of the balance recovered when a loan writes off, booked in the period it happens. A `recovery_amount` column is added if there is not one |
| **New loans per period** | 0 – 10% | Share of the opening pool written at **every cut-off after the first** |

Any control the configuration cannot honour is disabled with the reason —
"this configuration declares no state a loan cannot recover from, so nothing
counts as a default".

#### New loans — closed vs open pools

At **0** the pool is *closed*: every loan exists at the first cut-off and the
pool only shrinks as loans redeem and write off. That is right for a static
securitisation.

Above 0 the pool is *open*. A run starting in December 2024 and ageing 24 months
will hold loans written across 2025 and 2026 as well as the opening book. New
loans are drawn from the same distributions, stamped with the cut-off they arrive
at, and aged from there. They are **not** aged in the period they arrive — a loan
written this month has not also paid a month of interest.

They enter performing, with every upward-ticking counter at zero, and where the
configuration has an origination-date column they are dated to the period they
arrive, so seasoning and remaining term follow. The note tells you which columns
were dated.

The validator adapts: `closed_pool` is replaced by `entity_spans_contiguous`
(no entity vanishes and reappears) and `origination_window` (nothing joins
outside the window you declared).

#### Transition matrix

Each row is a state, each column where a loan might be next period. Rows must sum
to 1 — the total is live in the Σ column and turns red if it does not. Edit cells
directly for fine control; the default-rate slider is a shortcut that rescales
the whole thing.

### Tab 5: Schema

The distribution behind each sampled column, editable per generator kind:

| Generator | What you can edit here |
|---|---|
| `categorical` | The weight of each value (first 24 shown) |
| `scipy` | Each named parameter of the fitted distribution — `s`, `loc`, `scale`, `a`, `b` |
| `gaussian` | `mean`, `stddev` |
| `uniform` | `low`, `high` |
| `bernoulli` | `p`, the probability of the true value |
| `constant` | The value |
| `empirical` | Read-only summary — edit in the Advanced tab |

Each row shows the column's role, its generator kind, an `optional` badge if it
may be blanked, and a confidence badge. Low-confidence columns carry a **Review**
note saying what to check. **Filter columns…** narrows the list; 100 are shown at
a time.

### Tab 6: Advanced

The whole configuration as YAML, and it is what runs. Everything the forms do not
expose can be set here — see [the next section](#everything-only-the-yaml-exposes).

- **Apply** parses and validates. If it fails, nothing changes and every problem
  is listed.
- **Revert** throws away your text and re-renders from the forms.

### ➡ Generate data

Queues the run. Disabled while the configuration is invalid.

---

## Step 4 — Generate

Seven named stages, a progress bar and an estimated finish:

```
Reading schema  ·  Profiling sample  ·  Building configuration
Generating synthetic data  ·  Ageing portfolio  ·  Running validation
Preparing downloads
```

The first three happened in earlier steps and show as already done. The run
happens on a worker thread, so nothing depends on the browser staying open — the
estimate is a straight-line extrapolation from elapsed time, honest about being
crude.

If it fails, the error appears here in full. The commonest cause is a
configuration that is valid but impossible — for example CTGAN selected with no
sample, which is refused before the run rather than minutes into it.

---

## Step 5 — Results

### Summary tiles

**Rows generated**, **Columns**, **Entities** (everything that ever entered the
pool), **Written later** (only for an open pool), **Periods**, **Surviving**,
**Time taken**, **Validation**.

### Validation

`N of M invariants passed`. These are derived from your own configuration: if a
column is declared static, a check appears asserting it never changes; if a state
is declared terminal, a check appears asserting nothing survives it. Failures are
listed with their violating row counts.

Below it, what the randomness controls actually did — "5 columns reordered to
match the sample's correlation, 2 columns blanked" — and the scenario overlay if
one was applied.

### Charts

| Chart | What it shows |
|---|---|
| **Distribution comparison** | A histogram of one column, generated against your sample, on shared bins. Pick the column from the dropdown. Without a sample you get the generated series alone |
| **Delinquency curve** | Share of the *surviving* pool in each distressed state, period by period, plus a dashed total. Shares rather than counts, because a shrinking pool makes counts fall even when behaviour is unchanged |
| **LTV distribution** | Leverage at the first cut-off against the last. A current LTV is preferred over an original one, which never moves |
| **Pool balance over time** | Total outstanding balance per cut-off, with opening and closing figures, the pool factor and the surviving loan count |

A chart that cannot be drawn says why — "no column looks like a loan-to-value
ratio" — rather than rendering something meaningless.

### Generated data

A searchable, sortable window onto the panel. **Search** matches anywhere in any
column; click a header to sort; Previous/Next page through 25 rows at a time.
Search and sort run against the file, not the page, so a twelve-million-row panel
behaves like a twelve-thousand-row one. Blanked values show as a grey `null`.

---

## Step 6 — Download

| Button | Format | What it is |
|---|---|---|
| **Download CSV** | `.csv` | The whole panel as one file. Opens anywhere |
| **Download Parquet** | `.parquet` | Columnar and typed. The right choice for anything read by code |
| **Download Excel** | `.xlsx` | A workbook with the data on one sheet and its provenance — spec hash, seed, method, timings, validation verdict — on another. Capped at 1,000,000 rows, and the file says so if it truncated |
| **Download Configuration** | `.yaml` | The exact configuration that produced this data. Re-run it and get the same file back |
| **Download Validation report** | `.html` | A standalone page listing every invariant checked and its result, plus the pool composition per period. No external assets, no scripts — safe to email or attach to a review |

Below, **Per-period files**: the consolidated panel plus one file per cut-off,
exactly as the ageing engine wrote them.

**Start over** returns to Upload with a clean slate.

---

## Everything only the YAML exposes

The forms cover what most runs need. The Advanced tab reaches everything else.
Below is the full vocabulary, section by section.

### `meta`

| Field | Notes |
|---|---|
| `name` | Identifier. Short, lowercase, used in filenames |
| `title` | Human-readable name, shown wherever a person picks the spec |
| `asset_class` | Free text — `rmbs`, `auto`, `cre`, … |
| `regulatory_template` | Free text, e.g. `ESMA Annex 5 — Underlying exposures, automobile` |
| `description`, `source` | Free text |

### `entity`

| Field | Notes |
|---|---|
| `id_column` | Which column identifies an entity |
| `id_format` | Format string for generated identifiers, e.g. `GL{deal_year}_{seq:06d}`. `{seq}` is the 1-based row number; other placeholders resolve against `params` and `constants`. **Sequential identifiers cannot collide** — an 8-hex-character random id collides about 29 times in 500k draws |
| `time_column` | Which column holds the cut-off date |
| `calendar.start` | First cut-off, ISO 8601 |
| `calendar.periods` | How many cut-offs |
| `calendar.freq` | `month_end`, `quarter_end`, `year_end`, **`month_start`**, **`day`** — the last two are YAML-only |

### `params` and `constants`

`params` are free-form values usable in `id_format` and every expression.
`constants` are deal-level facts written to every row — originator name, currency,
country.

### `columns`

| Field | Notes |
|---|---|
| `name` | |
| `role` | `static` (fixed per entity) · `dynamic` (moves) · `derived` (computed, no generator) · `constant` · `helper` (intermediate, dropped before output) |
| `dtype` | `int` `float` `str` `category` `bool` `date` |
| `generator` | See below |
| `domain` | Allowed values. Enforced by the validator |
| `min` / `max` | Bounds. Respected by every generation method and by the randomness controls |
| `required` | Whether the missing-values control may blank it |
| `null_rate` | **Per-column** blank rate, overriding the global setting |
| `description`, `confidence`, `review` | Documentation and provenance |

**Generator kinds** — ten, of which the Schema tab edits six:

| Kind | Fields |
|---|---|
| `categorical` | `values`, `weights` |
| `conditional_categorical` | `parent`, `mapping` (parent value → candidate values), `weights`, `default`. Used for province → region: each parent value has its own pool |
| `scipy` | `dist` (any `scipy.stats` name), `params`, `decimals`, `clip_min`, `clip_max` |
| `gaussian` | `mean`, `stddev`, `decimals`, `clip_min`, `clip_max` |
| `uniform` | `low`, `high`, `decimals` |
| `bernoulli` | `p`, `true_value`, `false_value` |
| `empirical` | `values`, `weights`, `decimals` — resamples observed values, preserving point masses exactly |
| `sequence` | `prefix`, `start`, `width` |
| `uuid` | `prefix`, `short`, `uppercase` |
| `constant` | `value` |

### `derivations`

Deterministic columns computed from others. Four kinds:

| Kind | Fields |
|---|---|
| `expr` | A vectorised expression: `original_balance / (oltomv_original / 100.0)` |
| `when` | Ordered `rules` of `if` / `then`, plus `else`. First match wins |
| `bucket` | `bucket` (a named binning) + `source` (the column to bin) |
| `format` | `template` (a format string) + `args` (placeholder → expression), for date-proxy columns |

Each also takes `round`, `dtype`, and **`stage`**:

- `book` — period 0 only (origination facts)
- `period` — every ageing period (recomputed as balances move)
- `both`

Expressions are evaluated by a restricted AST walker — a spec is data, never
executable code. Available functions: `abs min max round floor ceil clip log
log10 exp sqrt power where isin coalesce isnull notnull int float str`, plus the
constants `pi` and `e`. `and` / `or` / `not` and `x if cond else y` work
element-wise.

### `buckets`

Reusable binning rules: `bins` (edges), `labels`, `right`, `include_lowest`.
`len(labels)` must equal `len(bins) - 1`.

### `lifecycle`

| Field | Notes |
|---|---|
| `states` | Ordered **best-first**. Scenarios read that order as severity |
| `state_column` | Which column holds the label |
| `transitions` | Square matrix; each row sums to 1 |
| `transition_states` | Which states the matrix covers. Defaults to states minus terminal |
| `absorbing` | Cannot be left, but stays in the pool — a defaulted loan still gets reported |
| `terminal` | Ends the entity's life. The row is written once, then drops out |
| `hazards` | See below |
| `state_fields` | state → `{column: forced value}`, applied after every transition. A forced value always wins |
| `initial_distribution` | Optional state mix at period 0 |

**Hazards** — events decided outside the matrix:

- `bernoulli`: `name`, `to_state`, and exactly one of `annual_rate` or
  `period_rate`; optional `from_states`, `excluded_states`
- `dwell_time`: `name`, `from_state`, `to_state`, `periods` — fires after N
  consecutive periods in a state

### `dynamics`

**`amortisation`** — `kind` is one of `annuity` · `linear` · `bullet` ·
`interest_only` · `revolving` · `depreciation` · `none`. Plus `balance`, `rate`,
`payment`, `term`, `only_when_state` (states in which a payment is assumed —
anything else freezes the balance), `flat_when` (an expression selecting rows that
never amortise), `rate_per_period`, `floor`.

**`indices`** — multiplicative overlays: `name`, `applies_to`, `kind`
(`constant_drift` or `series`), `annual`, `series`, `volatility`.

**`counters`** — `column`, and exactly one of `step` (fixed increment) or `expr`
(recomputed each period), plus `clip_min`, `clip_max`, `dtype`.

**`accruals`** — `column`, `add` (a column or a number), `when`
(`not_performing` · `in_states` · `always`), `states`, `reset_states`,
`performing_state`.

**`recovery`** — `rate`, `balance`, `target`, `on_states`.

### `generation`

| Field | Forms? | Notes |
|---|---|---|
| `method` | ✅ | |
| `noise`, `correlation`, `outliers`, `missing` | ✅ | |
| **`outlier_sigma`** | ❌ | How far into the tail an outlier is pushed. Default 4.0 |
| **`correlation_target`** | ❌ | `columns` + `matrix`, written by the profiler |
| **`polish_model`** | ❌ | `ctgan` or `tvae` |
| **`polish_epochs`** | ❌ | Default 300 |

### `originations`

| Field | Forms? | Notes |
|---|---|---|
| `rate` **or** `per_period` | ✅ (rate) | Exactly one. A rate is a share of the opening book |
| **`start_period`** | ❌ | First cut-off new entities appear at. Default 1 |
| **`end_period`** | ❌ | Last one. Default: every period |
| **`fresh`** | ❌ | Newly written rather than acquired. Default true |
| **`reset`** | ❌ | column → literal value forced on a new entity |
| **`reset_expr`** | ❌ | column → expression, with `period`, `period_year`, `period_month`, `period_day` available. This is how a loan written in June gets June's origination date — reset the date a derivation *reads*, and everything computed from it follows. Resetting a derived column directly does not work: the derivation recomputes it |

### `scenarios`

Named overlays. `default_multiplier` scales every transition that worsens an
entity's state; `prepayment_multiplier` scales hazard rates; `index_shift` is an
index name → additive change to its annual rate; `rate_shift` + `rate_columns`
add percentage points to interest-rate columns.

### `emit`

`filename` (placeholders `{name}` `{yyyy}` `{mm}` `{yyyymm}` `{yyyy_mm_dd}`
`{period}`), `column_order`, `formats` (`csv`, `parquet`), `panel_filename`,
`write_panel`, `float_format`.

### `validation`

**`checks`** — eight toggles, all on by default:
`static_columns_stable`, `ids_unique_per_period`, `closed_pool`,
`terminal_states_absorb`, `domains_respected`, `counters_step_correctly`,
`state_fields_applied`, `non_negative_balances`.

**`non_negative_columns`** — columns asserted never to go below zero. The
generation methods respect this as a floor, not just the validator.

**`custom`** — your own checks: `name`, `description`, and `sql`, a SELECT over
the relation `panel` returning violating rows. Zero rows means it passed.

---

## The command line

Everything the UI does, `sdd.api` does, and the CLI is a thin wrapper over it.

```bash
sdd ui                                open the web UI (--host, --port, --reload)
sdd packs                             list the bundled packs
sdd profile SAMPLE                    analyse a tape, print what it found
sdd design SAMPLE -o spec.yaml        analyse it and write a runnable spec
sdd check SPEC                        validate a spec without running it
sdd run SPEC -n 50000 -o ./out        generate and age
sdd validate SPEC PANEL               check a panel against its spec
sdd fidelity REFERENCE SYNTHETIC      score synthetic against real
```

`sdd run` takes `--num-records/-n`, `--out/-o`, `--seed`, `--periods`,
`--scenario`, `--backend` (`numpy` or `nemo`), `--skip-validation` and
`--quiet/-q`.

A configuration downloaded from the UI runs unchanged:

```bash
sdd run ~/Downloads/green_lion.yaml -n 100000 -o ./out
```

---

## Troubleshooting

**The page looks wrong after an update.** Static files are served with
`Cache-Control: no-cache`, so a hard reload is not normally needed. If you are
running an older build, restart `sdd ui`.

**"This configuration will not run."** The red badge lists every problem. They
are written to be actionable — *"transition matrix row 0 ('Performing') sums to
0.857500, expected 1.0"* — and none of the underlying validation library's
plumbing reaches you.

**A method is greyed out.** Sampling needs a sample; CTGAN and Hybrid need a
sample *and* `pip install 'sdd[deep]'`.

**The missing-values slider does nothing.** Every column is marked required. Go
back to Review and untick Required on the columns that may be blank.

**The correlation slider does nothing.** No sample was analysed, so no
relationship between columns was ever measured.

**Validation failed.** Open the report from the Download step: it names each
check, what it asserts, and how many rows violated it. A failure is usually a
configuration that contradicts itself — two rules writing the same column, or a
declared bound the generator can exceed.

**Excel says the file is truncated.** Excel cannot hold more than ~1,048,576
rows. The workbook's `about` sheet records how many were generated; use CSV or
parquet for the whole panel.

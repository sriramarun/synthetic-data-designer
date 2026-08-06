---
title: Synthetic Data Designer
emoji: 📊
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Design, generate and age synthetic loan tapes from a schema
---

# Synthetic Data Designer

Give it a schema and, if you have one, sample data. It works out how the data
behaves, generates a synthetic portfolio, and ages it forward period by period —
loans paying down, falling behind, prepaying, defaulting.

A six-step wizard: **Upload → Review → Configure → Generate → Results → Download**.

Two calibrated packs are bundled, so you can see the whole thing work without
uploading anything:

- **Dutch Green Loans — Residential Mortgages** (ESMA Annex 2, 71 columns)
- **European Auto Loans — ESMA Annex 5** (44 columns, depreciating collateral,
  balloon payments, recovery on write-off)

Load one on the first screen, press through to Generate, and you have a
validated multi-period panel with charts and five download formats.

## ⚠️ This is a shared, public demo

Anything you upload is written to this Space's disk, into a workspace shared
with every other visitor, and whoever operates this Space can read it. Storage
is temporary — it is wiped whenever the Space restarts or goes to sleep.

**Do not upload confidential data here.**

The tool is designed to run locally, where nothing leaves your machine:

```bash
pip install 'sdd[web]'
sdd ui
```

Source: <https://github.com/sriramarun/synthetic-data-designer>

## Limits on this instance

| | |
|---|---|
| Rows per run | 50,000 |
| Periods | 60 |
| Upload size | 50 MB |
| CTGAN / Hybrid methods | unavailable — they need PyTorch, which is too large and too slow for a basic CPU Space |

Run it locally for anything bigger. There are no limits there.

## What it does

- **Profiles** a sample: column types, static vs dynamic, distributions,
  the lifecycle and transition matrix, correlations — measured, not assumed
- **Generates** by one of six methods, from moment-matched normals to a deep
  tabular model, each written into the configuration as generators you can read
- **Ages** the portfolio forward with amortisation, indices, arrears, defaults,
  recoveries, and optionally new loans written during the window
- **Validates** the result against invariants derived from the configuration
  itself, and shows you every check
- **Exports** CSV, Parquet, Excel, the configuration as YAML, and a standalone
  validation report

Apache 2.0. Generalised from
[Algoritmica-ai/deeploans](https://github.com/Algoritmica-ai/deeploans).

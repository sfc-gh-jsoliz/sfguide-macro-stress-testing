# Macro Stress Testing

Stress-test a payment portfolio against the macro cycle — portfolio volume and credit health by credit tier and product, tied to CPI, unemployment, Fed funds, PCE, and consumer sentiment, with what-if scenarios and a grounded, self-verifying narrative.

Owner: Javier Soliz · Last reviewed: November 2026

This is not an official Snowflake product.

## What it shows

A payments processor's risk & FP&A team is asked the same question every planning cycle: *what happens to our book if the macro environment turns?* This pack answers it end to end:

- A **portfolio benchmark** by credit tier and product — transaction volume, approval rate, 30-day delinquency, charge-off — sitting alongside the monthly macro backdrop.
- **Sensitivity betas** (per-tier elasticities): approval vs Fed funds, delinquency and charge-off vs unemployment, volume vs consumer sentiment — the basis for what-if scenarios like `+200 bps Fed funds`.
- A **grounded stress note**: a draft written under strict data rules, then a verifier pass that flags it **Verified** or **Needs review** when any claim is unsupported or hedged.
- A **semantic view** and **Cortex Agent** you can explore conversationally in Snowsight.

## How it works

1. **Benchmark view** (`MACRO_STRESS_BENCHMARK`) — the synthetic portfolio by credit tier, product, and month joined to the monthly macro backdrop in one governed view; macro columns repeat across rows and are treated as non-additive.
2. **Semantic view** (`MACRO_STRESS_SV`) — business meaning over that benchmark. Cortex Analyst answers natural-language questions, with macro columns exposed as non-additive `AVG` metrics.
3. **Cortex Agent** (`MACRO_STRESS_AGENT`) — created by setup over the semantic view; explore it in Snowsight → AI & ML → Agents.
4. **Grounded draft + verifier** — the Streamlit app calls Cortex **`AI_COMPLETE`** to write the stress note under DATA RULES (copy figures exactly, no estimates, no hedging), then a second `AI_COMPLETE` pass checks every claim against the fact pack and marks the note Verified or Needs review. The app calls `AI_COMPLETE` directly; the agent and semantic view are separate objects you explore in Snowsight.

## Prerequisites

- Snowflake Enterprise Edition or higher, in a region where Cortex AISQL and Cortex Agents are available.
- A Snowflake account, with a role that can `CREATE DATABASE` and `CREATE WAREHOUSE`.
- Cortex AI functions available in your region (`AI_COMPLETE`; the app defaults to `claude-sonnet-4-5` — change the `MODEL` constant if your account differs).
- Streamlit in Snowflake.
- Nothing to buy — sample mode is fully synthetic (portfolio **and** macro indicators), so no Marketplace listing is required.

## Setup

1. **Sample mode (recommended first):** run [`app/setup_synthetic.sql`](app/setup_synthetic.sql). It creates the dedicated database, schema, and warehouse, then builds every object on a synthetic portfolio **and** synthetic macro indicators — no listing needed.
2. **Real macro data (optional):** [`app/setup.sql`](app/setup.sql) builds the same objects but points the macro side at **real** Cybersyn economic indicators. Mount the free `SNOWFLAKE_PUBLIC_DATA_FINANCIAL_PACKAGE` from the Marketplace first, then run it. This documents how to swap the synthetic macro table for real / free Marketplace data.
3. **Deploy the app:** inside `SNOWPACK_MACRO_STRESS.CORE`, create a stage, upload [`app/streamlit_app.py`](app/streamlit_app.py) to it, and `CREATE STREAMLIT` over it (or paste it into a new Streamlit in Snowsight with `SNOWPACK_MACRO_STRESS.CORE` as the location), running on `SNOWPACK_MACRO_STRESS_WH`. Keeping the stage and Streamlit object in `SNOWPACK_MACRO_STRESS.CORE` means `teardown.sql` removes them with the database.
4. **Open the notebook:** [`notebook/lab-macro-stress.ipynb`](notebook/) walks the pattern (benchmark view → semantic view → fact pack → grounded draft → verifier) cell by cell.

Both setup scripts build identical objects in `SNOWPACK_MACRO_STRESS.CORE`, so the notebook, semantic view, agent, and app are unchanged between modes.

## Customize with Cortex Code

Adapt this pack to your own book with [Cortex Code](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-code). Try prompts like:

- "Add a `+300 bps Fed funds` and a `-2 pp consumer sentiment` scenario to the what-if controls, and re-point the betas in `MACRO_STRESS_BETA` so Deep-subprime reacts about twice as hard as Prime."
- "Change the per-tier elasticity betas so charge-off is more sensitive to unemployment than to Fed funds, and update the stress-note fact pack to match."
- "Add a new credit tier `Near-prime` between Prime and Subprime across `MACRO_STRESS_SEGMENT`, `MACRO_STRESS_PORTFOLIO_MONTHLY`, and `MACRO_STRESS_BETA`, and regenerate the benchmark view."

## Objects created

| Object | Type | Purpose |
|---|---|---|
| `SNOWPACK_MACRO_STRESS` | Database | Dedicated, isolated database for this pack |
| `SNOWPACK_MACRO_STRESS.CORE` | Schema | Holds all objects below |
| `SNOWPACK_MACRO_STRESS_WH` | Warehouse | XSMALL, auto-suspend 60s, used to build and run the pack |
| `MACRO_STRESS_SEGMENT` | Table | Credit-tier dimension (Prime → Deep-subprime) |
| `MACRO_STRESS_PRODUCT` | Table | Payment-product dimension (Credit card, Debit, Prepaid, BNPL) |
| `MACRO_STRESS_INDICATOR` | Table | Macro indicator dimension (key → name/source/unit + Cybersyn variable) |
| `MACRO_STRESS_PORTFOLIO_MONTHLY` | Table | Synthetic portfolio fact by tier/product/month (swap for your own book) |
| `MACRO_STRESS_BETA` | Table | Per-tier elasticity betas for what-if stress sims |
| `MACRO_STRESS_INDICATORS` | Table (sample) / View (real) | Monthly macro indicators; synthetic in sample mode, real Cybersyn view in `setup.sql` |
| `MACRO_STRESS_BENCHMARK` | View | Wide pre-joined portfolio + macro backdrop view (macro columns non-additive) |
| `MACRO_STRESS_DATA_SOURCE_INFO` | View | Marker row describing the active data mode (sample vs real) |
| `MACRO_STRESS_SV` | Semantic view | Business layer over the benchmark for Cortex Analyst |
| `MACRO_STRESS_REPORTS` | Table | Stored stress notes with verification status |
| `MACRO_STRESS_AGENT` | Cortex Agent | Cortex Analyst agent over `MACRO_STRESS_SV`, explorable in Snowsight |
| `streamlit_app.py` | Streamlit app | Generates and verifies stress notes via `AI_COMPLETE` |

## Cost & time

Runs in a few minutes. The warehouse is XSMALL with a 60-second auto-suspend, and all sample queries operate over small tables (16 tier×product pools over 24 months). The Streamlit app's note generation calls `AI_COMPLETE` twice per run (draft + verify), which incurs Cortex token cost; everything else is standard warehouse compute.

## Known limitations

- The portfolio is **synthetic** (a processor's own book has no public source), and sample mode also uses synthetic macro indicators.
- The macro backdrop is joined to the portfolio by **month**, so each macro column repeats across tier×product rows and must be treated as non-additive (`AVG`, never `SUM`).
- The static Try page is a **mock** of the AI responses (illustrative, not live inference).
- This is illustrative example code, not production code — review and test before any production use.

## Cleanup

Run [`app/teardown.sql`](app/teardown.sql) to drop the dedicated database and warehouse and remove everything this pack created.

## Learn more

- [Snowflake Cortex AISQL (`AI_COMPLETE` and friends)](https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql)
- [Cortex Agents](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents)
- [Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst)
- [Streamlit in Snowflake](https://docs.snowflake.com/en/developer-guide/streamlit/about-streamlit)

## License

Apache 2.0 (see [`LICENSE`](LICENSE)). Illustrative example, as-is, not supported by Snowflake Support. Contains no customer data. Review and test before any production use.

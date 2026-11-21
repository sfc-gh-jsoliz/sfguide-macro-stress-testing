# Data: the synthetic book of Quillmar Payments

Everything in this folder is synthetic. Quillmar Payments is a fictional card, debit, prepaid and BNPL (buy now, pay later) processor; no real firm, customer or account is represented.

## How it is made

`python3 data/generate.py` builds the data with a fixed seed (`4242`) and a fixed row order, so re-running it gives byte-identical files. It writes:

| File | Rows | What it holds |
|---|---|---|
| `pools.csv` | 16 | The 4 credit tiers × 4 products, with holiday lift and average ticket |
| `portfolio_monthly.csv` | 384 | Volume, transactions, accounts, approval, 30-day delinquency and charge-off per pool, July 2024 to June 2026 |
| `indicators.csv` | 120 | Synthetic CPI, unemployment, Fed funds, PCE and consumer sentiment per month |
| `betas.csv` | 16 | Per-tier elasticities used by the what-if scenarios |
| `appetite.csv` | 4 | Illustrative monthly charge-off limit per tier; Watch starts at 80% of the limit |

The generator also writes the same rows into `docs/try/data/pack.json` (the hosted example) and into the `GENERATED DATA` blocks of `app/setup_synthetic.sql` and `app/setup.sql`, so Sample and Live mode show the same numbers. `app/setup.sql` keeps the synthetic portfolio but reads real macro indicators from the free Marketplace Financial Data Package.

`data/sample/` holds results captured from a Live run (`scripts/capture_sample.py`): stress notes written by `AI_COMPLETE`, verifier verdicts, Cortex Agent answers, and a parity check that the app's scenario math equals the `MACRO_STRESS_SCENARIO` table function.

## Planted stories

These are built into the generator on purpose. Figures are for this synthetic data set only.

1. **Holiday peak.** November and December volume is lifted by each pool's holiday lift (13% to 48%). Book volume goes from $4.94B in October 2025 to $6.14B in December 2025; BNPL rises 46% and debit 13% over the same two months.
2. **Post-holiday delinquency.** 30-day delinquency is 25% higher in January and February. Volume-weighted delinquency on credit products rises from 3.26% in December 2025 to 4.02% in January 2026.
3. **Charge-off lag.** Charge-off is 20% higher in February and March, a month after delinquency peaks (credit products: 1.73% in January 2026, 2.07% in February 2026).
4. **Steady growth.** Volume grows 0.4% a month and active accounts 0.3% a month, under ±2.5% noise.
5. **Risk rises down the tiers.** Deep-subprime betas are 10 to 20 times Prime's: approval falls 2.2 points per +1 pp Fed funds versus 0.2 for Prime, and charge-off rises 2.0 points per +1 pp unemployment versus 0.1.
6. **Debit and prepaid carry no credit risk.** Their delinquency and charge-off are zero; scenarios move only their volume.
7. **Appetite breaches under stress.** At June 2026 (the as-of month) nothing is on Watch at baseline. Base case (+200 bps, +1 pp): 2 pools on Watch (Deep-subprime credit card and BNPL). Severe (+300 bps, +2 pp): Deep-subprime BNPL breaches its 12% limit and 3 pools are on Watch. 2008-style (+3 pp unemployment): 3 breaches (Subprime BNPL, Deep-subprime credit card and BNPL) and 2 on Watch.
8. **The verifier catches an unsupported forecast.** With "Inject an unsupported forecast" on, the app appends "Charge-offs will likely double if a recession hits next quarter." to the draft; the verifier should mark the note Needs review.

## Macro indicators (synthetic mode)

Deterministic curves over the 24 months: CPI rises from 310 to 319.66, PCE from 122 to 128.44, Fed funds eases from 5.33% to a 4.30% floor, unemployment cycles between 4.0% and 4.3%, and consumer sentiment swings around 67 (69.95 in June 2026). In `app/setup.sql` these come from the real indicators instead, so the macro charts change while the portfolio and betas stay the same.

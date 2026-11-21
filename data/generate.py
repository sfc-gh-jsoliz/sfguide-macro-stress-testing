#!/usr/bin/env python3
"""Seeded, deterministic generator for the Macro Stress Testing pack.

Writes the synthetic book of the fictional processor Quillmar Payments:
  data/*.csv                      the tables, for reading and diffing
  docs/try/data/pack.json         the same rows for the hosted no-account example
  app/setup_synthetic.sql and app/setup.sql
                                  INSERT blocks between the GENERATED markers

Re-running gives byte-identical output (fixed seed, fixed row order).
Usage: python3 data/generate.py
"""
import csv
import json
import math
import random
import re
from datetime import date
from pathlib import Path

SEED = 4242
ROOT = Path(__file__).resolve().parent.parent

TIERS = ["Prime", "Near-prime", "Subprime", "Deep-subprime"]
PRODUCTS = ["Credit card", "Debit", "Prepaid", "BNPL"]

# Per-(tier, product) drivers, kept from the original pack.
# base_vol_musd, approval_base, delinq_base, chargeoff_base, holiday_lift, avg_ticket, base_accounts
DRIVERS = {
    ("Prime", "Credit card"): (900, 0.95, 0.009, 0.003, 0.28, 95, 220000),
    ("Prime", "Debit"): (700, 0.995, 0.0, 0.0, 0.15, 48, 300000),
    ("Prime", "Prepaid"): (90, 0.99, 0.0, 0.0, 0.35, 55, 60000),
    ("Prime", "BNPL"): (160, 0.90, 0.012, 0.004, 0.45, 120, 70000),
    ("Near-prime", "Credit card"): (520, 0.84, 0.026, 0.011, 0.26, 78, 160000),
    ("Near-prime", "Debit"): (480, 0.99, 0.0, 0.0, 0.15, 40, 240000),
    ("Near-prime", "Prepaid"): (140, 0.98, 0.0, 0.0, 0.33, 50, 90000),
    ("Near-prime", "BNPL"): (180, 0.80, 0.030, 0.013, 0.46, 95, 95000),
    ("Subprime", "Credit card"): (300, 0.64, 0.058, 0.034, 0.24, 62, 120000),
    ("Subprime", "Debit"): (260, 0.985, 0.0, 0.0, 0.14, 35, 180000),
    ("Subprime", "Prepaid"): (200, 0.975, 0.0, 0.0, 0.34, 46, 140000),
    ("Subprime", "BNPL"): (150, 0.62, 0.065, 0.038, 0.47, 80, 90000),
    ("Deep-subprime", "Credit card"): (120, 0.46, 0.095, 0.072, 0.22, 52, 70000),
    ("Deep-subprime", "Debit"): (150, 0.98, 0.0, 0.0, 0.13, 32, 120000),
    ("Deep-subprime", "Prepaid"): (170, 0.97, 0.0, 0.0, 0.33, 42, 130000),
    ("Deep-subprime", "BNPL"): (70, 0.44, 0.105, 0.080, 0.48, 70, 60000),
}

# Elasticities, kept from the original pack: (metric, indicator, {tier: beta}, note)
BETAS = [
    ("approval_rate", "FED_FUNDS", {"Prime": -0.002, "Near-prime": -0.006, "Subprime": -0.014, "Deep-subprime": -0.022},
     "approval_rate change per +1pp Fed funds"),
    ("charge_off_rate", "UNEMPLOYMENT", {"Prime": 0.001, "Near-prime": 0.004, "Subprime": 0.012, "Deep-subprime": 0.020},
     "charge_off_rate change per +1pp unemployment"),
    ("delinquency_rate_30d", "UNEMPLOYMENT", {"Prime": 0.002, "Near-prime": 0.006, "Subprime": 0.016, "Deep-subprime": 0.026},
     "30d delinquency change per +1pp unemployment"),
    ("transaction_volume_usd", "SENTIMENT", {"Prime": 0.004, "Near-prime": 0.005, "Subprime": 0.006, "Deep-subprime": 0.007},
     "volume pct change per +1pt consumer sentiment"),
]

# Risk appetite: monthly charge-off limit per tier (new in v2). Watch starts at 80% of the limit.
APPETITE = {"Prime": 0.010, "Near-prime": 0.030, "Subprime": 0.070, "Deep-subprime": 0.120}
WATCH_SHARE = 0.80

INDICATORS = [
    ("CPI", "CPI: All items (SA, 1982-84=100)", "Bureau of Labor Statistics", "Index", "CUSRSA0SA01982-84.M", False),
    ("UNEMPLOYMENT", "Unemployment Rate (SA)", "Bureau of Labor Statistics", "Percent", "LNS14000000.M_SA", True),
    ("FED_FUNDS", "Effective Federal Funds Rate (EFFR)", "New York Fed", "Percent", "EFFR_PCT", True),
    ("PCE", "PCE Price Index (SA, 2017=100)", "Bureau of Economic Analysis", "Index", "BEA_NIPA_PCE_RELEASE_DPCERG_M_SA_2017", False),
    ("SENTIMENT", "Index of Consumer Sentiment", "University of Michigan", "Index", "the_index_of_consumer_sentiment", False),
]

# Scenario presets shown in the app (Fed funds bps, unemployment pp)
PRESETS = [("Mild", 50, 0.5), ("Base case", 200, 1.0), ("Severe", 300, 2.0), ("2008-style", 0, 3.0)]


def month_ends():
    out = []
    for i in range(24):
        y, m = 2024 + (6 + i) // 12, (6 + i) % 12 + 1
        nxt = date(y + (m // 12), m % 12 + 1, 1)
        out.append(date.fromordinal(nxt.toordinal() - 1))
    return out


def synthetic_indicator(key, i):
    if key == "CPI":
        v = 310 + 0.42 * i
    elif key == "UNEMPLOYMENT":
        v = 4.0 + 0.3 * abs((i % 6) - 3) / 3.0
    elif key == "FED_FUNDS":
        v = max(4.3, 5.33 - 0.045 * i)
    elif key == "PCE":
        v = 122 + 0.28 * i
    else:
        v = 67 + 3 * math.sin(i / 3.0)
    return round(v, 2)


def generate():
    rng = random.Random(SEED)
    months = month_ends()
    pools, portfolio = [], []
    sid = {t: n + 1 for n, t in enumerate(TIERS)}
    pid = {p: n + 1 for n, p in enumerate(PRODUCTS)}
    for t in TIERS:
        for p in PRODUCTS:
            vol, appr, dq, co, lift, ticket, accts = DRIVERS[(t, p)]
            pools.append(dict(segment_id=sid[t], credit_tier=t, product_id=pid[p], product=p, holiday_lift=lift,
                              avg_ticket=ticket))
            for i, me in enumerate(months):
                noise_v = rng.randint(-25, 25) / 1000.0
                noise_a = rng.randint(-15, 15) / 1000.0
                noise_d = rng.randint(-8, 8) / 100.0
                noise_c = rng.randint(-8, 8) / 100.0
                holiday = 1 + lift if me.month in (11, 12) else 1.0
                volume = vol * 1e6 * (1 + 0.004 * i) * holiday * (1 + noise_v)
                portfolio.append(dict(
                    segment_id=sid[t], credit_tier=t, product_id=pid[p], product=p, month_end=me.isoformat(),
                    transaction_volume_usd=int(round(volume)),
                    txn_count=int(round(volume / ticket)),
                    active_accounts=int(round(accts * (1 + 0.003 * i))),
                    approval_rate=round(min(0.999, max(0.30, appr + noise_a)), 4),
                    delinquency_rate_30d=round(dq * (1.25 if me.month in (1, 2) else 1.0) * (1 + noise_d), 4),
                    charge_off_rate=round(co * (1.20 if me.month in (2, 3) else 1.0) * (1 + noise_c), 4),
                ))
    indicators = [dict(indicator_key=k, indicator_name=n, source=s, unit=u, month_end=me.isoformat(),
                       indicator_value=synthetic_indicator(k, i))
                  for (k, n, s, u, _, _) in INDICATORS for i, me in enumerate(months)]
    betas = [dict(credit_tier=t, product="ALL", portfolio_metric=m, indicator_key=ind, beta=b[t], note=note)
             for (m, ind, b, note) in BETAS for t in TIERS]
    appetite = [dict(credit_tier=t, charge_off_limit=APPETITE[t], watch_share=WATCH_SHARE) for t in TIERS]
    return pools, portfolio, indicators, betas, appetite


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def sql_lit(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def insert(table, rows, cols):
    lines = [f"INSERT INTO {table} ({', '.join(cols)}) VALUES"]
    vals = ["  (" + ", ".join(sql_lit(r[c]) for c in cols) + ")" for r in rows]
    return "\n".join(lines) + "\n" + ",\n".join(vals) + ";\n"


def sql_block(portfolio, indicators, betas, appetite, with_indicators):
    pcols = ["segment_id", "credit_tier", "product_id", "product", "month_end", "transaction_volume_usd", "txn_count",
             "active_accounts", "approval_rate", "delinquency_rate_30d", "charge_off_rate"]
    out = ["-- Generated by data/generate.py (seed %d). Do not edit by hand; re-run the generator.\n" % SEED,
           insert("MACRO_STRESS_PORTFOLIO_MONTHLY", portfolio, pcols),
           insert("MACRO_STRESS_BETA", betas, ["credit_tier", "product", "portfolio_metric", "indicator_key", "beta", "note"]),
           insert("MACRO_STRESS_APPETITE", appetite, ["credit_tier", "charge_off_limit", "watch_share"])]
    if with_indicators:
        out.append(insert("MACRO_STRESS_INDICATORS", indicators,
                          ["indicator_key", "indicator_name", "source", "unit", "month_end", "indicator_value"]))
    return "\n".join(out)


def splice(path, block):
    text = path.read_text()
    new = re.sub(r"(-- BEGIN GENERATED DATA\n).*?(-- END GENERATED DATA)", lambda m: m.group(1) + block + m.group(2),
                 text, flags=re.S)
    if new == text and "-- BEGIN GENERATED DATA" not in text:
        raise SystemExit(f"markers missing in {path}")
    path.write_text(new)


def main():
    pools, portfolio, indicators, betas, appetite = generate()
    data = ROOT / "data"
    write_csv(data / "portfolio_monthly.csv", portfolio)
    write_csv(data / "indicators.csv", indicators)
    write_csv(data / "betas.csv", betas)
    write_csv(data / "appetite.csv", appetite)
    write_csv(data / "pools.csv", pools)
    pack = dict(seed=SEED, firm="Quillmar Payments", asOf=portfolio[-1]["month_end"], tiers=TIERS, products=PRODUCTS,
                presets=[dict(name=n, fedBps=f, unPp=u) for n, f, u in PRESETS],
                pools=pools, portfolio=portfolio, indicators=indicators, betas=betas, appetite=appetite)
    out = ROOT / "docs" / "try" / "data"
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.json").write_text(json.dumps(pack, separators=(",", ":")) + "\n")
    splice(ROOT / "app" / "setup_synthetic.sql", sql_block(portfolio, indicators, betas, appetite, True))
    splice(ROOT / "app" / "setup.sql", sql_block(portfolio, indicators, betas, appetite, False))
    print(f"{len(portfolio)} portfolio rows, {len(indicators)} indicator rows, {len(betas)} betas, seed {SEED}")


if __name__ == "__main__":
    main()

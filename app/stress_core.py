"""Shared logic for the Macro Stress Testing app: data access, scenario math, fact pack and prompts.

Sample mode reads the seeded CSVs in data/. Live mode reads the same tables in Snowflake and runs
the scenario in the MACRO_STRESS_SCENARIO SQL table function. Both paths return the same shapes.
"""
import json
import re
import time
from pathlib import Path

import pandas as pd

SCHEMA = "SNOWPACK_MACRO_STRESS.CORE"
AGENT = f"{SCHEMA}.MACRO_STRESS_AGENT"
MODEL = "claude-sonnet-4-5"
TIERS = ["Prime", "Near-prime", "Subprime", "Deep-subprime"]
PRODUCTS = ["Credit card", "Debit", "Prepaid", "BNPL"]
PRESETS = [("Mild", 50, 0.5), ("Base case", 200, 1.0), ("Severe", 300, 2.0), ("2008-style", 0, 3.0)]
INJECTED_CLAIM = "Charge-offs will likely double if a recession hits next quarter."
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------- data access
class SampleSource:
    mode = "sample"

    def __init__(self):
        self.portfolio = pd.read_csv(DATA_DIR / "portfolio_monthly.csv", parse_dates=["month_end"])
        self.indicators = pd.read_csv(DATA_DIR / "indicators.csv", parse_dates=["month_end"])
        self.betas = pd.read_csv(DATA_DIR / "betas.csv")
        self.appetite = pd.read_csv(DATA_DIR / "appetite.csv")

    def benchmark(self):
        macro = self.indicators.pivot_table(index="month_end", columns="indicator_key", values="indicator_value").reset_index()
        macro = macro.rename(columns={"CPI": "cpi_index", "UNEMPLOYMENT": "unemployment_rate", "FED_FUNDS": "fed_funds_rate",
                                      "PCE": "pce_index", "SENTIMENT": "consumer_sentiment"})
        df = self.portfolio.merge(macro, on="month_end")
        df["is_holiday_season"] = df["month_end"].dt.month.isin([11, 12])
        return df

    def scenario(self, fed_bps, un_pp):
        return scenario_frame(self.portfolio, self.betas, self.appetite, fed_bps, un_pp)

    def data_note(self):
        return "synthetic", "Bundled synthetic portfolio and synthetic macro indicators (seed 4242)."


class LiveSource:
    mode = "live"

    def __init__(self, session):
        self.session = session

    def _df(self, sql, params=None):
        df = self.session.sql(sql, params=params or []).to_pandas()
        df.columns = [c.lower() for c in df.columns]
        return df

    def benchmark(self):
        df = self._df(f"SELECT * FROM {SCHEMA}.MACRO_STRESS_BENCHMARK ORDER BY segment_id, product_id, month_end")
        df["month_end"] = pd.to_datetime(df["month_end"])
        return df

    def scenario(self, fed_bps, un_pp):
        return self._df(f"SELECT * FROM TABLE({SCHEMA}.MACRO_STRESS_SCENARIO(?::FLOAT, ?::FLOAT))",
                        [float(fed_bps), float(un_pp)])

    def data_note(self):
        r = self.session.sql(f"SELECT mode, note FROM {SCHEMA}.MACRO_STRESS_DATA_SOURCE_INFO").collect()[0]
        return r[0], r[1]

    def complete(self, prompt):
        return self.session.sql("SELECT AI_COMPLETE(?, ?)", params=[MODEL, prompt]).collect()[0][0]

    def save_report(self, subject, scenario, draft, status, verdict):
        self.session.sql(
            f"INSERT INTO {SCHEMA}.MACRO_STRESS_REPORTS (subject, scenario, draft_md, report_md, status, edits_applied, "
            "residual_hedges, verifier_json) SELECT ?, ?, ?, ?, ?, ?, ?, PARSE_JSON(?)",
            params=[subject, scenario, draft, draft, status, len(verdict.get("edits", [])),
                    verdict.get("residual_hedges"), json.dumps(verdict)]).collect()

    def reports(self):
        return self._df(f"SELECT run_id, subject, scenario, status, edits_applied, residual_hedges, report_md, "
                        f"verifier_json, created_at FROM {SCHEMA}.MACRO_STRESS_REPORTS ORDER BY created_at DESC")

    def ask_agent(self, question):
        body = json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]})
        raw = self.session.sql(
            f"SELECT TRY_PARSE_JSON(SNOWFLAKE.CORTEX.DATA_AGENT_RUN('{AGENT}', ?, TRUE)):content::STRING",
            params=[body]).collect()[0][0]
        return parse_agent_content(raw)


def parse_agent_content(raw):
    """Final answer = every text and table block after the last tool block; SQL comes from tool results."""
    blocks = json.loads(raw or "[]")
    last_tool = max([i for i, b in enumerate(blocks) if b.get("type") in ("tool_use", "tool_result")], default=-1)
    text, tables, sql = [], [], []
    for b in blocks:
        if b.get("type") == "tool_result":
            for c in b["tool_result"].get("content", []):
                q = (c.get("json") or {}).get("sql")
                if q:
                    sql.append(q)
    for b in blocks[last_tool + 1:]:
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "table":
            tables.append(b.get("table", {}))
    return {"text": "\n\n".join(t for t in text if t).strip(), "sql": sql, "tables": tables}


# ---------------------------------------------------------------- scenario math (mirrors MACRO_STRESS_SCENARIO)
def scenario_frame(portfolio, betas, appetite, fed_bps, un_pp):
    latest = portfolio[portfolio["month_end"] == portfolio["month_end"].max()].copy()
    b = betas.pivot_table(index="credit_tier", columns="portfolio_metric", values="beta")
    a = appetite.set_index("credit_tier")
    fed, un = fed_bps / 100.0, float(un_pp)
    sent = -(1.5 * fed + 4 * un)
    rows = []
    for _, r in latest.iterrows():
        t = r["credit_tier"]
        co_s = max(0.0, r["charge_off_rate"] + b.loc[t, "charge_off_rate"] * un) if r["charge_off_rate"] > 0 else 0.0
        limit, watch = a.loc[t, "charge_off_limit"], a.loc[t, "watch_share"]
        status = ("No credit risk" if r["charge_off_rate"] == 0 else "Breach" if co_s > limit
                  else "Watch" if co_s >= watch * limit else "Within appetite")
        rows.append(dict(
            credit_tier=t, product=r["product"], month_end=r["month_end"],
            volume_base=float(r["transaction_volume_usd"]),
            volume_stress=r["transaction_volume_usd"] * (1 + b.loc[t, "transaction_volume_usd"] * sent),
            approval_base=r["approval_rate"],
            approval_stress=min(0.999, max(0.30, r["approval_rate"] + b.loc[t, "approval_rate"] * fed)),
            delinquency_base=r["delinquency_rate_30d"],
            delinquency_stress=max(0.0, r["delinquency_rate_30d"] + b.loc[t, "delinquency_rate_30d"] * un)
            if r["delinquency_rate_30d"] > 0 else 0.0,
            charge_off_base=r["charge_off_rate"], charge_off_stress=co_s, charge_off_limit=limit, status=status,
            sentiment_delta=sent))
    return pd.DataFrame(rows)


def weighted(df, col, mask=None):
    d = df if mask is None else df[mask]
    w = d["volume_base"]
    return float((d[col] * w).sum() / w.sum()) if w.sum() else 0.0


def aggregate(sc, tier="All tiers"):
    d = sc if tier == "All tiers" else sc[sc["credit_tier"] == tier]
    credit = d["charge_off_base"] > 0
    return dict(
        volume_base=float(d["volume_base"].sum()), volume_stress=float(d["volume_stress"].sum()),
        approval_base=weighted(d, "approval_base"), approval_stress=weighted(d, "approval_stress"),
        delinquency_base=weighted(d, "delinquency_base", credit), delinquency_stress=weighted(d, "delinquency_stress", credit),
        charge_off_base=weighted(d, "charge_off_base", credit), charge_off_stress=weighted(d, "charge_off_stress", credit),
        breaches=int((d["status"] == "Breach").sum()), watches=int((d["status"] == "Watch").sum()))


# ---------------------------------------------------------------- fact pack and prompts
def pct(v, nd=2):
    return f"{v * 100:.{nd}f}%"


def usd(v):
    return f"${v / 1e9:.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:.1f}M"


def fact_pack(bench, sc, tier, fed_bps, un_pp):
    """Numbered facts the note may use. Every value is pre-formatted so the model copies, never computes."""
    latest = bench["month_end"].max()
    macro = bench[bench["month_end"] == latest].iloc[0]
    agg = aggregate(sc, tier)
    sent = float(sc["sentiment_delta"].iloc[0])
    facts = []

    def add(label, value, source):
        facts.append(dict(id=f"F{len(facts) + 1}", label=label, value=value, source=source))

    add("As-of month", latest.strftime("%Y-%m"), "MACRO_STRESS_BENCHMARK")
    add("Scenario", f"+{fed_bps:g} bps Fed funds, +{un_pp:g} pp unemployment", "app input")
    add("Fed funds, baseline -> scenario", f"{macro['fed_funds_rate']:.2f}% -> {macro['fed_funds_rate'] + fed_bps / 100:.2f}%",
        "MACRO_STRESS_INDICATORS")
    add("Unemployment, baseline -> scenario", f"{macro['unemployment_rate']:.2f}% -> {macro['unemployment_rate'] + un_pp:.2f}%",
        "MACRO_STRESS_INDICATORS")
    add("Consumer sentiment, baseline -> modeled", f"{macro['consumer_sentiment']:.1f} -> {macro['consumer_sentiment'] + sent:.1f}",
        "MACRO_STRESS_SCENARIO")
    scope = "the whole book" if tier == "All tiers" else f"the {tier} tier"
    add(f"Monthly volume, {scope}, baseline -> scenario", f"{usd(agg['volume_base'])} -> {usd(agg['volume_stress'])}",
        "MACRO_STRESS_SCENARIO")
    add(f"Approval rate (volume-weighted), {scope}", f"{pct(agg['approval_base'])} -> {pct(agg['approval_stress'])}",
        "MACRO_STRESS_SCENARIO")
    add(f"30-day delinquency (credit products), {scope}", f"{pct(agg['delinquency_base'])} -> {pct(agg['delinquency_stress'])}",
        "MACRO_STRESS_SCENARIO")
    add(f"Charge-off (credit products), {scope}", f"{pct(agg['charge_off_base'])} -> {pct(agg['charge_off_stress'])}",
        "MACRO_STRESS_SCENARIO")
    add(f"Pools in Breach / Watch, {scope}", f"{agg['breaches']} / {agg['watches']}", "MACRO_STRESS_APPETITE")
    d = sc if tier == "All tiers" else sc[sc["credit_tier"] == tier]
    for _, r in d[d["charge_off_base"] > 0].sort_values("charge_off_stress", ascending=False).iterrows():
        add(f"{r['credit_tier']} {r['product']}: charge-off baseline -> scenario (limit), status",
            f"{pct(r['charge_off_base'])} -> {pct(r['charge_off_stress'])} (limit {pct(r['charge_off_limit'], 1)}), {r['status']}",
            "MACRO_STRESS_SCENARIO")
    b = bench if tier == "All tiers" else bench[bench["credit_tier"] == tier]
    vol = b.groupby("month_end")["transaction_volume_usd"].sum()
    dec, nov_prev = vol[vol.index.month == 12].iloc[-1], vol[vol.index.month == 10].iloc[-1]
    add(f"Holiday peak, {scope}: December 2025 vs October 2025 volume", f"{usd(dec)} vs {usd(nov_prev)} ({(dec / nov_prev - 1) * 100:+.1f}%)",
        "MACRO_STRESS_BENCHMARK")
    cr = b[b["delinquency_rate_30d"] > 0]
    dq = cr.groupby("month_end").apply(lambda g: (g["delinquency_rate_30d"] * g["transaction_volume_usd"]).sum()
                                       / g["transaction_volume_usd"].sum())
    jan, dec_dq = dq[dq.index == pd.Timestamp("2026-01-31")].iloc[0], dq[dq.index == pd.Timestamp("2025-12-31")].iloc[0]
    add(f"Post-holiday delinquency, {scope}: January 2026 vs December 2025", f"{pct(jan)} vs {pct(dec_dq)}",
        "MACRO_STRESS_BENCHMARK")
    return facts


def draft_prompt(facts, tier):
    return (
        "You are a payments risk and finance analyst at a processor. Write a short stress note for the risk committee.\n"
        "DATA RULES: use ONLY the numbered FACTS below and copy values exactly as written. Put the fact id in square brackets "
        "right after every number you use, for example 4.30% [F3]. Do not compute new numbers, estimate, forecast, or hedge "
        "(no likely, approximately, around, could, may). Do not use outside benchmarks. Do not name any real company.\n"
        f"Subject: {tier}.\n"
        "OUTPUT markdown: a '# ' title; one bold takeaway sentence; a table with columns Metric | Baseline | Scenario | Fact for "
        "volume, approval, delinquency and charge-off; a '## What moves' section with 3 bullets (the most stressed pools and "
        "their appetite status, the holiday-season effect, the post-holiday delinquency lift); and a final '## Data coverage' "
        "line naming the as-of month and the sources.\n\nFACTS:\n"
        + "\n".join(f"[{f['id']}] {f['label']}: {f['value']} (source {f['source']})" for f in facts))


def verify_prompt(facts, draft):
    return (
        "You are a fact-checker. Compare the DRAFT against the numbered FACTS (the only allowed source). Return ONLY a JSON "
        "object with keys: status ('verified' if every number and claim is supported by FACTS and there are no hedge or "
        "forecast words such as likely, approximately, around, could, will; else 'needs_review'), residual_hedges (integer), "
        "claims_checked (integer, the number of numeric claims you checked), edits (array of short strings naming each "
        "unsupported or hedged claim; empty if none).\n\nFACTS:\n"
        + "\n".join(f"[{f['id']}] {f['label']}: {f['value']}" for f in facts) + f"\n\nDRAFT:\n{draft}")


def parse_verdict(raw):
    s, e = raw.find("{"), raw.rfind("}")
    try:
        v = json.loads(raw[s:e + 1])
    except Exception:
        return {"status": "needs_review", "residual_hedges": None, "claims_checked": 0, "edits": ["Verifier output was not valid JSON"]}
    v["status"] = "verified" if v.get("status") == "verified" else "needs_review"
    v.setdefault("edits", [])
    return v


def clean_completion(raw):
    """AI_COMPLETE returns a JSON string literal; unwrap it."""
    try:
        v = json.loads(raw)
        return v if isinstance(v, str) else raw
    except Exception:
        return raw


def inject(draft):
    return re.sub(r"(\n## Data coverage)", "\n- " + INJECTED_CLAIM + r"\n\1", draft, count=1, flags=re.I) \
        if re.search(r"\n## Data coverage", draft, re.I) else draft + "\n- " + INJECTED_CLAIM + "\n"


def run_note(src, bench, tier, fed_bps, un_pp, inject_claim=False):
    sc = src.scenario(fed_bps, un_pp)
    facts = fact_pack(bench, sc, tier, fed_bps, un_pp)
    t0 = time.time()
    draft = clean_completion(src.complete(draft_prompt(facts, tier)))
    if inject_claim:
        draft = inject(draft)
    verdict = parse_verdict(clean_completion(src.complete(verify_prompt(facts, draft))))
    return dict(tier=tier, fedBps=fed_bps, unPp=un_pp, injected=inject_claim, facts=facts, draft=draft, verdict=verdict,
                latencyMs=int((time.time() - t0) * 1000))


# ---------------------------------------------------------------- Live session
def live_session():
    """Snowpark session: the active one inside Streamlit in Snowflake, else key-pair auth from environment variables.

    Local Live mode needs SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and SNOWFLAKE_PRIVATE_KEY_PATH (a key registered on that user).
    Never put credentials in the browser or the repository.
    """
    import os
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        pass
    need = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise RuntimeError("Live mode needs " + ", ".join(missing) + ". See README > Run Live mode locally.")
    from snowflake.snowpark import Session
    return Session.builder.configs({
        "account": os.environ["SNOWFLAKE_ACCOUNT"], "user": os.environ["SNOWFLAKE_USER"],
        "authenticator": "SNOWFLAKE_JWT", "private_key_file": os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        "role": os.environ.get("SNOWFLAKE_ROLE", "SNOWPACK_MACRO_STRESS_ROLE"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", "SNOWPACK_MACRO_STRESS_WH"),
        "database": "SNOWPACK_MACRO_STRESS", "schema": "CORE"}).create()

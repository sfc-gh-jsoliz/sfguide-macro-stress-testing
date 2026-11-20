import json
import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Macro Stress Testing", layout="wide")

SCHEMA = "SNOWPACK_MACRO_STRESS.CORE"
MODEL = "claude-sonnet-4-5"
session = get_active_session()
_rerun = getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))
BADGE = {"verified": "\u2705 Verified", "needs_review": "\u26a0\ufe0f Needs review"}


def sql_df(q, p=None):
    df = session.sql(q, params=p or []).to_pandas()
    df.columns = [c.lower() for c in df.columns]
    return df


def data_mode():
    try:
        r = session.sql(f"SELECT mode, note FROM {SCHEMA}.MACRO_STRESS_DATA_SOURCE_INFO").collect()[0]
        return r[0], r[1]
    except Exception:
        return "unknown", ""


def load_reports():
    return sql_df(f"SELECT run_id, subject, status, edits_applied, residual_hedges, report_md, created_at FROM {SCHEMA}.MACRO_STRESS_REPORTS ORDER BY created_at DESC")


def fact_pack():
    return session.sql(f"""
        WITH w AS (SELECT MIN(month_end) a, MAX(month_end) z FROM {SCHEMA}.MACRO_STRESS_BENCHMARK)
        SELECT ARRAY_AGG(OBJECT_CONSTRUCT(
          'credit_tier', s.credit_tier, 'product', s.product, 'month', s.month_end::string,
          'transaction_volume_usd', s.transaction_volume_usd, 'approval_rate', s.approval_rate,
          'delinquency_rate_30d', s.delinquency_rate_30d, 'charge_off_rate', s.charge_off_rate,
          'unemployment_rate', s.unemployment_rate, 'fed_funds_rate', s.fed_funds_rate,
          'consumer_sentiment', s.consumer_sentiment, 'is_holiday_season', s.is_holiday_season))
        FROM {SCHEMA}.MACRO_STRESS_BENCHMARK s JOIN w ON s.month_end IN (w.a, w.z)
    """).collect()[0][0]


def draft_note(fp):
    rules = (
        "You are a payments risk & finance analyst. Write a concise portfolio stress note using ONLY the FACTS JSON.\n"
        "DATA RULES: use only numbers in FACTS, copy exactly; rates are decimals (0.04 = 4%); unemployment and fed_funds are in percentage points; "
        "dollars are USD, express $B/$M; macro values are a non-additive backdrop (do not sum); no estimates, no hedging, no outside benchmarks; missing = n/d.\n"
        "OUTPUT markdown starting with '# ', a one-line takeaway (how volume & credit health moved with the macro backdrop), a table "
        "(credit tier | product | volume start->now | approval | delinquency | charge-off), 3 bullets (incl. holiday-season effect and the most downturn-sensitive tier), ending with '## Data Coverage'."
    )
    return session.sql("SELECT AI_COMPLETE(?, ?)", params=[MODEL, rules + f"\n\nFACTS: {fp}"]).collect()[0][0]


def verify(fp, draft):
    vp = (
        "You are a fact-checker. Compare the DRAFT against the FACTS JSON (only allowed source). Return ONLY a JSON object with keys "
        "status ('verified' if every number/claim is supported and no hedge words like likely/approximately/around/could, else 'needs_review'), "
        "residual_hedges (int), edits (array of strings).\n\n" + f"FACTS: {fp}\n\nDRAFT:\n{draft}"
    )
    raw = session.sql("SELECT AI_COMPLETE(?, ?)", params=[MODEL, vp]).collect()[0][0]
    s, e = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[s:e + 1])
    except Exception:
        return {"status": "needs_review", "residual_hedges": None, "edits": ["unparseable verifier output"]}


st.title("Macro Stress Testing")
st.caption("A payments processor's portfolio volume & credit health by tier/product vs the macro backdrop (CPI, unemployment, Fed funds, PCE, sentiment).")
_mode, _note = data_mode()
if _mode == "synthetic":
    st.warning("Sample mode \u2014 synthetic macro. " + _note)
elif _mode == "marketplace":
    st.info("Live macro \u2014 " + _note)

if "view" not in st.session_state:
    st.session_state["view"] = "gallery"

with st.sidebar:
    st.subheader("Generate a note")
    st.caption("Benchmarks the portfolio against the macro backdrop and writes a verified stress note.")
    if st.button("Run macro-stress analysis", type="primary", use_container_width=True):
        with st.spinner("Fact pack \u2192 grounded draft \u2192 verify (~20-40s)..."):
            fp = fact_pack()
            draft = draft_note(fp)
            v = verify(fp, draft)
            status = "verified" if v.get("status") == "verified" else "needs_review"
            session.sql(
                f"INSERT INTO {SCHEMA}.MACRO_STRESS_REPORTS (subject, draft_md, report_md, status, edits_applied, residual_hedges) SELECT ?, ?, ?, ?, ?, ?",
                params=["Portfolio macro stress", draft, draft, status, len(v.get("edits", [])), v.get("residual_hedges")],
            ).collect()
        st.success(f"Done \u2014 {BADGE[status]}")
        st.session_state["view"] = "gallery"
        _rerun()

if st.session_state["view"].startswith("report:"):
    run_id = st.session_state["view"].split(":", 1)[1]
    rpt = load_reports()
    row = rpt[rpt["run_id"] == run_id]
    if st.button("\u2190 Back to gallery"):
        st.session_state["view"] = "gallery"
        _rerun()
    if not row.empty:
        r = row.iloc[0]
        st.markdown(f"**{BADGE.get(r['status'], r['status'])}**  &nbsp; residual hedges: {r['residual_hedges']}  |  flagged: {r['edits_applied']}")
        st.divider()
        st.markdown(r["report_md"])
else:
    reports = load_reports()
    if reports.empty:
        st.info("No notes yet. Use the sidebar to generate one.")
    else:
        cols = st.columns(3)
        for i, (_, r) in enumerate(reports.iterrows()):
            with cols[i % 3]:
                c = st.container()
                c.markdown(f"**{BADGE.get(r['status'], r['status'])}**")
                c.markdown(f"**{r['subject']}**")
                c.caption(f"{str(r['created_at'])[:16]} \u00b7 {r['edits_applied']} flagged")
                if c.button("Open report", key=f"open_{r['run_id']}", use_container_width=True):
                    st.session_state["view"] = f"report:{r['run_id']}"
                    _rerun()
                c.markdown("---")

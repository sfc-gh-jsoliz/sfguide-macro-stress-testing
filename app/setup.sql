-- ============================================================================
-- Macro Stress Testing — setup.sql  (real-data mode: REAL macro indicators)
-- ----------------------------------------------------------------------------
-- Macro Stress Testing for a payments processor's risk & FP&A team: link holiday
-- payment volume and portfolio health (approval, delinquency, charge-off) to the
-- macro backdrop (CPI, unemployment, Fed funds, PCE, consumer sentiment), and run
-- what-if stress sims via per-segment elasticity betas.
--
-- The processor's own book has no public source, so MACRO_STRESS_PORTFOLIO_MONTHLY
-- is SYNTHETIC in both modes. Here the MACRO side is REAL Cybersyn economic indicators from the free Marketplace
-- "Financial Data Package". Prereq: mount SNOWFLAKE_PUBLIC_DATA_FINANCIAL_PACKAGE. Run
-- setup_synthetic.sql instead for a fully synthetic, no-listing sample.
--
-- Objects in SNOWPACK_MACRO_STRESS.CORE (prefix MACRO_STRESS_):
--   MACRO_STRESS_SEGMENT, MACRO_STRESS_PRODUCT, MACRO_STRESS_INDICATOR (dims),
--   MACRO_STRESS_PORTFOLIO_MONTHLY (synthetic fact), MACRO_STRESS_BETA (elasticities),
--   MACRO_STRESS_INDICATORS (VIEW over real Cybersyn indicators), MACRO_STRESS_BENCHMARK (wide view),
--   MACRO_STRESS_DATA_SOURCE_INFO, MACRO_STRESS_SV, MACRO_STRESS_REPORTS, MACRO_STRESS_AGENT.
-- ============================================================================

-- Isolated environment (dedicated database, schema, and warehouse) --
CREATE DATABASE IF NOT EXISTS SNOWPACK_MACRO_STRESS;
CREATE SCHEMA IF NOT EXISTS SNOWPACK_MACRO_STRESS.CORE;
CREATE WAREHOUSE IF NOT EXISTS SNOWPACK_MACRO_STRESS_WH
  WITH WAREHOUSE_SIZE='XSMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=TRUE;
USE WAREHOUSE SNOWPACK_MACRO_STRESS_WH;
USE SCHEMA SNOWPACK_MACRO_STRESS.CORE;

-- Type-switch safety (INDICATORS is a TABLE in synthetic mode, a VIEW in real mode) --
DROP VIEW  IF EXISTS SNOWPACK_MACRO_STRESS.CORE.MACRO_STRESS_INDICATORS;
DROP TABLE IF EXISTS SNOWPACK_MACRO_STRESS.CORE.MACRO_STRESS_INDICATORS;

-- 1. Dimensions --------------------------------------------------------------
CREATE OR REPLACE TABLE MACRO_STRESS_SEGMENT (segment_id INT, credit_tier STRING);
INSERT INTO MACRO_STRESS_SEGMENT VALUES
  (1,'Prime'),(2,'Near-prime'),(3,'Subprime'),(4,'Deep-subprime');

CREATE OR REPLACE TABLE MACRO_STRESS_PRODUCT (product_id INT, product STRING);
INSERT INTO MACRO_STRESS_PRODUCT VALUES
  (1,'Credit card'),(2,'Debit'),(3,'Prepaid'),(4,'BNPL');

-- Hardcoded macro indicator dim (indicator_key -> name/source/unit + Cybersyn variable).
-- scale_pct=TRUE means the raw Cybersyn value is a fraction (0.04) shown as pct points (4.0).
CREATE OR REPLACE TABLE MACRO_STRESS_INDICATOR (
  indicator_key STRING, indicator_name STRING, source STRING, unit STRING,
  cybersyn_variable STRING, scale_pct BOOLEAN
);
INSERT INTO MACRO_STRESS_INDICATOR VALUES
  ('CPI','CPI: All items (SA, 1982-84=100)','Bureau of Labor Statistics','Index','CUSRSA0SA01982-84.M',FALSE),
  ('UNEMPLOYMENT','Unemployment Rate (SA)','Bureau of Labor Statistics','Percent','LNS14000000.M_SA',TRUE),
  ('FED_FUNDS','Effective Federal Funds Rate (EFFR)','New York Fed','Percent','EFFR_PCT',TRUE),
  ('PCE','PCE Price Index (SA, 2017=100)','Bureau of Economic Analysis','Index','BEA_NIPA_PCE_RELEASE_DPCERG_M_SA_2017',FALSE),
  ('SENTIMENT','Index of Consumer Sentiment','University of Michigan','Index','the_index_of_consumer_sentiment',FALSE);

-- Per-(tier,product) synthetic drivers (deterministic). Debit/Prepaid carry no credit risk.
CREATE OR REPLACE TEMP TABLE MACRO_STRESS_drivers (
  segment_id INT, credit_tier STRING, product_id INT, product STRING,
  base_vol_musd FLOAT, approval_base FLOAT, delinq_base FLOAT, chargeoff_base FLOAT,
  holiday_lift FLOAT, avg_ticket FLOAT, base_accounts INT
) AS
SELECT * FROM VALUES
  (1,'Prime',1,'Credit card', 900,0.95,0.009,0.003,0.28, 95,220000),
  (1,'Prime',2,'Debit',       700,0.995,0.000,0.000,0.15, 48,300000),
  (1,'Prime',3,'Prepaid',      90,0.99,0.000,0.000,0.35, 55, 60000),
  (1,'Prime',4,'BNPL',        160,0.90,0.012,0.004,0.45,120, 70000),
  (2,'Near-prime',1,'Credit card',520,0.84,0.026,0.011,0.26, 78,160000),
  (2,'Near-prime',2,'Debit',      480,0.99,0.000,0.000,0.15, 40,240000),
  (2,'Near-prime',3,'Prepaid',    140,0.98,0.000,0.000,0.33, 50, 90000),
  (2,'Near-prime',4,'BNPL',       180,0.80,0.030,0.013,0.46, 95, 95000),
  (3,'Subprime',1,'Credit card',300,0.64,0.058,0.034,0.24, 62,120000),
  (3,'Subprime',2,'Debit',      260,0.985,0.000,0.000,0.14, 35,180000),
  (3,'Subprime',3,'Prepaid',    200,0.975,0.000,0.000,0.34, 46,140000),
  (3,'Subprime',4,'BNPL',       150,0.62,0.065,0.038,0.47, 80, 90000),
  (4,'Deep-subprime',1,'Credit card',120,0.46,0.095,0.072,0.22, 52, 70000),
  (4,'Deep-subprime',2,'Debit',      150,0.98,0.000,0.000,0.13, 32,120000),
  (4,'Deep-subprime',3,'Prepaid',    170,0.97,0.000,0.000,0.33, 42,130000),
  (4,'Deep-subprime',4,'BNPL',        70,0.44,0.105,0.080,0.48, 70, 60000)
  AS d(segment_id, credit_tier, product_id, product, base_vol_musd, approval_base,
       delinq_base, chargeoff_base, holiday_lift, avg_ticket, base_accounts);

-- 24 month-ends ending 2026-06-30
CREATE OR REPLACE TEMP TABLE MACRO_STRESS_months AS
SELECT LAST_DAY(DATEADD(month, SEQ4(), '2024-07-01')) AS month_end, SEQ4() AS m_idx
FROM TABLE(GENERATOR(ROWCOUNT => 24));

-- 2. Synthetic portfolio fact (holiday peak Nov-Dec; delinquency lags up Jan-Feb) --
CREATE OR REPLACE TABLE MACRO_STRESS_PORTFOLIO_MONTHLY
  COMMENT = 'SYNTHETIC: this processor''s payment portfolio by credit tier, product and month. Replace with your own authorization/settlement data (same columns) to go live.'
AS
SELECT
  d.segment_id, d.credit_tier, d.product_id, d.product, m.month_end,
  ROUND(d.base_vol_musd*1e6 * (1 + 0.004*m.m_idx)
        * (CASE WHEN MONTH(m.month_end) IN (11,12) THEN 1+d.holiday_lift ELSE 1 END)
        * (1 + UNIFORM(-25,25,RANDOM(4242))/1000.0), 0)                         AS transaction_volume_usd,
  ROUND((d.base_vol_musd*1e6 * (1 + 0.004*m.m_idx)
        * (CASE WHEN MONTH(m.month_end) IN (11,12) THEN 1+d.holiday_lift ELSE 1 END)
        * (1 + UNIFORM(-25,25,RANDOM(4242))/1000.0)) / NULLIF(d.avg_ticket,0), 0) AS txn_count,
  ROUND(d.base_accounts * (1 + 0.003*m.m_idx), 0)                                AS active_accounts,
  LEAST(0.999, GREATEST(0.30, ROUND(d.approval_base + UNIFORM(-15,15,RANDOM(77))/1000.0, 4))) AS approval_rate,
  ROUND(d.delinq_base    * (CASE WHEN MONTH(m.month_end) IN (1,2) THEN 1.25 ELSE 1 END)
                         * (1 + UNIFORM(-8,8,RANDOM(88))/100.0), 4)              AS delinquency_rate_30d,
  ROUND(d.chargeoff_base * (CASE WHEN MONTH(m.month_end) IN (2,3) THEN 1.20 ELSE 1 END)
                         * (1 + UNIFORM(-8,8,RANDOM(99))/100.0), 4)              AS charge_off_rate
FROM MACRO_STRESS_drivers d CROSS JOIN MACRO_STRESS_months m;

-- 3. Sensitivity betas (elasticities for what-if stress sims; subprime betas larger) --
CREATE OR REPLACE TABLE MACRO_STRESS_BETA (
  credit_tier STRING, product STRING, portfolio_metric STRING, indicator_key STRING, beta FLOAT, note STRING
);
INSERT INTO MACRO_STRESS_BETA VALUES
  ('Prime','ALL','approval_rate','FED_FUNDS',-0.002,'approval_rate change per +1pp Fed funds'),
  ('Near-prime','ALL','approval_rate','FED_FUNDS',-0.006,'approval_rate change per +1pp Fed funds'),
  ('Subprime','ALL','approval_rate','FED_FUNDS',-0.014,'approval_rate change per +1pp Fed funds'),
  ('Deep-subprime','ALL','approval_rate','FED_FUNDS',-0.022,'approval_rate change per +1pp Fed funds'),
  ('Prime','ALL','charge_off_rate','UNEMPLOYMENT',0.001,'charge_off_rate change per +1pp unemployment'),
  ('Near-prime','ALL','charge_off_rate','UNEMPLOYMENT',0.004,'charge_off_rate change per +1pp unemployment'),
  ('Subprime','ALL','charge_off_rate','UNEMPLOYMENT',0.012,'charge_off_rate change per +1pp unemployment'),
  ('Deep-subprime','ALL','charge_off_rate','UNEMPLOYMENT',0.020,'charge_off_rate change per +1pp unemployment'),
  ('Prime','ALL','delinquency_rate_30d','UNEMPLOYMENT',0.002,'30d delinquency change per +1pp unemployment'),
  ('Near-prime','ALL','delinquency_rate_30d','UNEMPLOYMENT',0.006,'30d delinquency change per +1pp unemployment'),
  ('Subprime','ALL','delinquency_rate_30d','UNEMPLOYMENT',0.016,'30d delinquency change per +1pp unemployment'),
  ('Deep-subprime','ALL','delinquency_rate_30d','UNEMPLOYMENT',0.026,'30d delinquency change per +1pp unemployment'),
  ('Prime','ALL','transaction_volume_usd','SENTIMENT',0.004,'volume pct change per +1pt consumer sentiment'),
  ('Near-prime','ALL','transaction_volume_usd','SENTIMENT',0.005,'volume pct change per +1pt consumer sentiment'),
  ('Subprime','ALL','transaction_volume_usd','SENTIMENT',0.006,'volume pct change per +1pt consumer sentiment'),
  ('Deep-subprime','ALL','transaction_volume_usd','SENTIMENT',0.007,'volume pct change per +1pt consumer sentiment');

-- 4. Macro side -- REAL Cybersyn economic indicators (PIT deduped to latest vintage) --
CREATE OR REPLACE VIEW MACRO_STRESS_INDICATORS
  COMMENT = 'REAL monthly macro indicators from SNOWFLAKE_PUBLIC_DATA_FINANCIAL_PACKAGE (Cybersyn economic indicators, PIT deduped by _EFFECTIVE_START_TIMESTAMP; daily EFFR rolled to monthly average; Percent series scaled to pct points).'
AS
WITH ts AS (
  SELECT variable, date, value,
         ROW_NUMBER() OVER (PARTITION BY variable, date ORDER BY _EFFECTIVE_START_TIMESTAMP DESC) AS rn
  FROM SNOWFLAKE_PUBLIC_DATA_FINANCIAL_PACKAGE.COMMERCIAL_BANKING.FINANCIAL_ECONOMIC_INDICATORS_TIMESERIES_PIT
  WHERE variable IN (SELECT cybersyn_variable FROM MACRO_STRESS_INDICATOR)
)
SELECT d.indicator_key, d.indicator_name, d.source, d.unit,
       LAST_DAY(ts.date) AS month_end,
       ROUND(AVG(ts.value) * (CASE WHEN d.scale_pct THEN 100 ELSE 1 END), 4) AS indicator_value
FROM ts JOIN MACRO_STRESS_INDICATOR d ON d.cybersyn_variable = ts.variable
WHERE ts.rn = 1
GROUP BY d.indicator_key, d.indicator_name, d.source, d.unit, d.scale_pct, LAST_DAY(ts.date);

-- 5. Benchmark wide view (single pre-joined view for the semantic view) -------
--    Fact grain = (segment_id, product_id, month_end). Macro pre-aggregated to (month_end)
--    then joined on month_end, so each macro column repeats across segment x product rows
--    and MUST be exposed as a non-additive AVG metric (never SUM) in the SV.
CREATE OR REPLACE VIEW MACRO_STRESS_BENCHMARK
  COMMENT = 'Synthetic payment portfolio by credit tier/product/month joined to the macro backdrop (monthly). Macro columns are non-additive.'
AS
WITH macro AS (
  SELECT month_end,
    AVG(CASE WHEN indicator_key='CPI'          THEN indicator_value END) AS cpi_index,
    AVG(CASE WHEN indicator_key='UNEMPLOYMENT' THEN indicator_value END) AS unemployment_rate,
    AVG(CASE WHEN indicator_key='FED_FUNDS'    THEN indicator_value END) AS fed_funds_rate,
    AVG(CASE WHEN indicator_key='PCE'          THEN indicator_value END) AS pce_index,
    AVG(CASE WHEN indicator_key='SENTIMENT'    THEN indicator_value END) AS consumer_sentiment
  FROM MACRO_STRESS_INDICATORS
  GROUP BY month_end
)
SELECT f.segment_id, f.credit_tier, f.product_id, f.product, f.month_end,
       f.transaction_volume_usd, f.txn_count, f.active_accounts,
       f.approval_rate, f.delinquency_rate_30d, f.charge_off_rate,
       ROUND(f.transaction_volume_usd / NULLIF(f.txn_count,0), 2) AS avg_ticket_usd,
       (MONTH(f.month_end) IN (11,12))                            AS is_holiday_season,
       mo.cpi_index, mo.unemployment_rate, mo.fed_funds_rate, mo.pce_index, mo.consumer_sentiment
FROM MACRO_STRESS_PORTFOLIO_MONTHLY f
JOIN macro mo USING (month_end);

-- 6. Data-source marker -------------------------------------------------------
CREATE OR REPLACE VIEW MACRO_STRESS_DATA_SOURCE_INFO AS
SELECT 'marketplace' AS mode,
       'Synthetic payment portfolio stress-tested against REAL Cybersyn economic indicators (free Financial Data Package): CPI (BLS), Unemployment Rate (BLS), Effective Fed Funds Rate (NY Fed), PCE Price Index (BEA), Index of Consumer Sentiment (U. Michigan).' AS note;

-- 7. Semantic view (single wide view; macro columns are non-additive AVG) -----
CREATE OR REPLACE SEMANTIC VIEW MACRO_STRESS_SV
  TABLES ( ms AS SNOWPACK_MACRO_STRESS.CORE.MACRO_STRESS_BENCHMARK PRIMARY KEY (segment_id, product_id, month_end)
           WITH SYNONYMS = ('macro stress','portfolio vs macro','stress test','portfolio health')
           COMMENT = 'Payments portfolio volume and credit health by credit tier, product and month vs the macro backdrop' )
  FACTS (
    ms.transaction_volume_usd AS TRANSACTION_VOLUME_USD,
    ms.txn_count AS TXN_COUNT,
    ms.active_accounts AS ACTIVE_ACCOUNTS,
    ms.approval_rate AS APPROVAL_RATE,
    ms.delinquency_rate_30d AS DELINQUENCY_RATE_30D,
    ms.charge_off_rate AS CHARGE_OFF_RATE,
    ms.avg_ticket_usd AS AVG_TICKET_USD,
    ms.cpi_index AS CPI_INDEX,
    ms.unemployment_rate AS UNEMPLOYMENT_RATE,
    ms.fed_funds_rate AS FED_FUNDS_RATE,
    ms.pce_index AS PCE_INDEX,
    ms.consumer_sentiment AS CONSUMER_SENTIMENT
  )
  DIMENSIONS (
    ms.credit_tier AS CREDIT_TIER WITH SYNONYMS = ('segment','risk tier','credit segment') COMMENT = 'Credit risk tier',
    ms.product AS PRODUCT WITH SYNONYMS = ('payment product','card type') COMMENT = 'Payment product',
    ms.month_end AS MONTH_END WITH SYNONYMS = ('month','period','as of'),
    ms.report_year AS YEAR(MONTH_END) WITH SYNONYMS = ('year'),
    ms.report_quarter AS QUARTER(MONTH_END) WITH SYNONYMS = ('quarter'),
    ms.is_holiday_season AS IS_HOLIDAY_SEASON WITH SYNONYMS = ('holiday','peak season','nov-dec') COMMENT = 'TRUE for November/December'
  )
  METRICS (
    ms.total_volume AS SUM(ms.transaction_volume_usd) COMMENT = 'Total transaction volume (USD)',
    ms.total_txns AS SUM(ms.txn_count) COMMENT = 'Total transactions',
    ms.total_accounts AS SUM(ms.active_accounts) COMMENT = 'Active accounts (sum across tier/product pools)',
    ms.avg_approval_rate AS AVG(ms.approval_rate) COMMENT = 'Average approval rate',
    ms.avg_delinquency_30d AS AVG(ms.delinquency_rate_30d) COMMENT = 'Average 30-day delinquency rate',
    ms.avg_charge_off_rate AS AVG(ms.charge_off_rate) COMMENT = 'Average charge-off rate',
    ms.avg_cpi AS AVG(ms.cpi_index) COMMENT = 'Average CPI index (non-additive macro backdrop)',
    ms.avg_unemployment AS AVG(ms.unemployment_rate) COMMENT = 'Average unemployment rate, pct points (non-additive)',
    ms.avg_fed_funds AS AVG(ms.fed_funds_rate) COMMENT = 'Average Fed funds rate, pct points (non-additive)',
    ms.avg_pce AS AVG(ms.pce_index) COMMENT = 'Average PCE price index (non-additive)',
    ms.avg_consumer_sentiment AS AVG(ms.consumer_sentiment) COMMENT = 'Average consumer sentiment (non-additive)'
  )
  COMMENT = 'Macro stress semantic view (synthetic portfolio vs macro indicators). Macro columns are non-additive AVG metrics.';

-- 8. Output table + agent -----------------------------------------------------
CREATE TABLE IF NOT EXISTS MACRO_STRESS_REPORTS (
  run_id STRING DEFAULT UUID_STRING(), subject STRING, draft_md STRING, report_md STRING,
  status STRING, edits_applied INT, residual_hedges INT, created_at TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE AGENT MACRO_STRESS_AGENT
  COMMENT = 'Macro Stress Testing agent (Cortex Analyst over MACRO_STRESS_SV)'
  PROFILE = '{"display_name": "Macro Stress Agent", "color": "blue"}'
  FROM SPECIFICATION
  $$
  models:
    orchestration: auto
  instructions:
    response: 'You are a payments risk and finance analyst. Use ONLY figures from the Analyst tool. Macro columns (CPI, unemployment, Fed funds, PCE, consumer sentiment) are non-additive -- use their AVG metrics, never sum them. Rates are decimals (0.04 = 4%); unemployment and Fed funds are in percentage points. Express USD in $B/$M. Never invent numbers. Be concise and cite the month.'
    orchestration: 'For any portfolio volume, approval, delinquency, charge-off, holiday-season, or macro-backdrop question use the MacroStress Analyst tool. For what-if elasticity ("+200 bps Fed funds") reference MACRO_STRESS_BETA.'
    sample_questions:
      - question: 'How did holiday-season (Nov-Dec) transaction volume track against consumer sentiment over the last two years?'
      - question: 'Which credit tiers show the highest 30-day delinquency during rising unemployment?'
  tools:
    - tool_spec:
        type: cortex_analyst_text_to_sql
        name: MacroStress
        description: 'Synthetic payment portfolio (volume, approval, delinquency, charge-off) by credit tier/product/month vs real macro indicators (CPI, unemployment, Fed funds, PCE, consumer sentiment).'
  tool_resources:
    MacroStress:
      semantic_view: SNOWPACK_MACRO_STRESS.CORE.MACRO_STRESS_SV
  $$;

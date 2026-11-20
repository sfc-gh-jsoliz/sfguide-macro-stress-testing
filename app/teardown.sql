-- ============================================================================
-- Macro Stress Testing — teardown.sql
-- ----------------------------------------------------------------------------
-- Removes everything created by setup_synthetic.sql / setup.sql by dropping the
-- dedicated database and warehouse. Irreversible — run only when you are done.
-- ============================================================================

DROP DATABASE IF EXISTS SNOWPACK_MACRO_STRESS;
DROP WAREHOUSE IF EXISTS SNOWPACK_MACRO_STRESS_WH;

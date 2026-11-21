-- ============================================================================
-- Macro Stress Testing — teardown.sql
-- ----------------------------------------------------------------------------
-- Removes everything created by setup_synthetic.sql / setup.sql: the dedicated
-- database (with its tables, views, function, semantic view, agent, stage and
-- Streamlit app), the warehouse, and the pack role. Irreversible.
-- Objects are owned by the pack role, so they are dropped as that role first.
-- ============================================================================

USE ROLE SNOWPACK_MACRO_STRESS_ROLE;
DROP DATABASE IF EXISTS SNOWPACK_MACRO_STRESS;
DROP WAREHOUSE IF EXISTS SNOWPACK_MACRO_STRESS_WH;

USE ROLE ACCOUNTADMIN;
DROP ROLE IF EXISTS SNOWPACK_MACRO_STRESS_ROLE;

SHOW DATABASES LIKE 'SNOWPACK_MACRO_STRESS';
SHOW WAREHOUSES LIKE 'SNOWPACK_MACRO_STRESS_WH';
SHOW ROLES LIKE 'SNOWPACK_MACRO_STRESS_ROLE';

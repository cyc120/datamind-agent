\connect datamind_meta
-- Own only this synthetic schema; the application does not need database CREATE.
CREATE SCHEMA IF NOT EXISTS demo_industrial AUTHORIZATION datamind_app;
GRANT USAGE ON SCHEMA demo_industrial TO readonly_user;

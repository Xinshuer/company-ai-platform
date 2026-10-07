-- 000 core (frozen): schemas, session-variable helpers used by every RLS policy.
-- Runs as corpai_owner. The app role gets USAGE on schemas here; table grants live with each table.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS trace;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE SCHEMA IF NOT EXISTS crm;
CREATE SCHEMA IF NOT EXISTS eng;
CREATE SCHEMA IF NOT EXISTS legal;
CREATE SCHEMA IF NOT EXISTS fin;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS metrics;
CREATE SCHEMA IF NOT EXISTS lg;

GRANT USAGE ON SCHEMA core, audit, trace, rag, crm, eng, legal, fin, hr, ops, metrics, lg TO corpai_app;

-- Unset variables mean "nobody": no groups, clearance 0. That is what makes RLS fail closed.
CREATE OR REPLACE FUNCTION core.cur_groups() RETURNS text[]
LANGUAGE sql STABLE AS $$
  SELECT coalesce(string_to_array(nullif(current_setting('app.groups', true), ''), ','), '{}'::text[])
$$;

CREATE OR REPLACE FUNCTION core.cur_clearance() RETURNS int
LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('app.clearance', true), '')::int, 0)
$$;

CREATE OR REPLACE FUNCTION core.cur_user() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.user_id', true), '') $$;

CREATE OR REPLACE FUNCTION core.cur_role() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.role', true), '') $$;

CREATE OR REPLACE FUNCTION core.cur_dept() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.dept', true), '') $$;

CREATE OR REPLACE FUNCTION core.cur_run() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.run_id', true), '') $$;

-- The standard read check for any labelled row: clearance ceiling AND at least one shared ACL group.
CREATE OR REPLACE FUNCTION core.can_read(row_level smallint, row_acl text[]) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT row_level <= core.cur_clearance() AND row_acl && core.cur_groups()
$$;

GRANT EXECUTE ON FUNCTION core.cur_groups(), core.cur_clearance(), core.cur_user(), core.cur_role(),
  core.cur_dept(), core.cur_run(), core.can_read(smallint, text[]) TO corpai_app;

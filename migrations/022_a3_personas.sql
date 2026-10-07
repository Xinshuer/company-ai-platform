-- 022 A3 identity: the demo persona list for the login picker.
-- SECURITY DEFINER because corpai_app cannot read core.users. It exposes only users that carry a
-- persona_key and only non-sensitive columns; Python refuses to call it unless demo login is on.

CREATE OR REPLACE FUNCTION core.list_personas()
RETURNS TABLE (user_id text, name text, title text, dept text, role text, persona_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
  SELECT u.user_id, u.name, u.title, u.dept, u.role, u.persona_key
  FROM core.users u
  WHERE u.persona_key IS NOT NULL AND u.active
  ORDER BY u.user_id
$$;

REVOKE ALL ON FUNCTION core.list_personas() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.list_personas() TO corpai_app;

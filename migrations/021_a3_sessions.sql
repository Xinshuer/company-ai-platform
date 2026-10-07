-- 021 A3 identity: login sessions behind the session JWT.
-- The JWT alone cannot be withdrawn before it expires; the sid row can. corpai_app gets EXECUTE on
-- the functions below and no table rights, so a request path can open, check and revoke a session
-- but never list sessions or touch another column.

CREATE TABLE IF NOT EXISTS core.sessions (
  sid        text PRIMARY KEY,
  user_id    text NOT NULL REFERENCES core.users(user_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON core.sessions (user_id) WHERE revoked_at IS NULL;

ALTER TABLE core.sessions ENABLE ROW LEVEL SECURITY;
-- No policy and no grants for corpai_app: owner-only data.

-- Only active human users can log in; service accounts never get a browser session.
CREATE OR REPLACE FUNCTION core.session_open(p_sid text, p_user_id text, p_ttl_s int)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = core, pg_temp AS $$
BEGIN
  IF p_sid IS NULL OR p_ttl_s IS NULL OR p_ttl_s <= 0 OR p_ttl_s > 8 * 3600 THEN
    RETURN false;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM core.users u
                 WHERE u.user_id = p_user_id AND u.active AND u.role <> 'service') THEN
    RETURN false;
  END IF;
  -- opportunistic cleanup keeps the table small without a cron job
  DELETE FROM core.sessions WHERE expires_at < now() - interval '1 day';
  INSERT INTO core.sessions (sid, user_id, expires_at)
  VALUES (p_sid, p_user_id, now() + make_interval(secs => p_ttl_s));
  RETURN true;
END
$$;

-- Valid = exists for this user, not revoked, not expired, and the user is still active.
CREATE OR REPLACE FUNCTION core.session_check(p_sid text, p_user_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1 FROM core.sessions s JOIN core.users u ON u.user_id = s.user_id
    WHERE s.sid = p_sid AND s.user_id = p_user_id
      AND s.revoked_at IS NULL AND s.expires_at > now() AND u.active)
$$;

-- Logout: the caller presents the sid and the user it belongs to (both from a verified session
-- JWT), and only that pair is revoked. A sid alone, or another user's id, revokes nothing.
CREATE OR REPLACE FUNCTION core.session_revoke(p_sid text, p_user_id text)
RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = core, pg_temp AS $$
  WITH r AS (UPDATE core.sessions SET revoked_at = now()
             WHERE sid = p_sid AND user_id = p_user_id AND revoked_at IS NULL RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM r)
$$;

-- Offboarding / incident response: end every session of a user. Only an active platform admin
-- (role AND group, like ops.set_switch) or the owner role (directory jobs) may do this; otherwise
-- any request path could log anyone out, the security auditor in an investigation included.
CREATE OR REPLACE FUNCTION core.session_revoke_user(p_user_id text)
RETURNS int
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = core, pg_temp AS $$
DECLARE
  v_n int;
BEGIN
  IF NOT pg_has_role(session_user, 'corpai_owner', 'MEMBER')
     AND (core.cur_role() IS DISTINCT FROM 'platform_admin'
          OR NOT ('role:platform_admin' = ANY (core.cur_groups()))
          OR core.cur_clearance() <= 0) THEN
    RAISE EXCEPTION 'only the platform admin may end another user''s sessions' USING ERRCODE = '42501';
  END IF;
  UPDATE core.sessions SET revoked_at = now() WHERE user_id = p_user_id AND revoked_at IS NULL;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END
$$;

REVOKE ALL ON FUNCTION core.session_open(text, text, int), core.session_check(text, text),
  core.session_revoke(text, text), core.session_revoke_user(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.session_open(text, text, int), core.session_check(text, text),
  core.session_revoke(text, text), core.session_revoke_user(text) TO corpai_app;

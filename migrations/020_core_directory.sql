-- 020 core (frozen): the employee directory. Populated by corpai.synth; read by corpai.identity.
-- The app role has no table access. It resolves principals only through core.principal_row(),
-- a SECURITY DEFINER function, because authentication happens before a principal exists.

CREATE TABLE IF NOT EXISTS core.users (
  user_id     text PRIMARY KEY,                 -- "u0001"; service accounts "svc:<job>"
  name        text NOT NULL,
  email       text NOT NULL UNIQUE,             -- @kestrelbyte.example
  dept        text NOT NULL CHECK (dept IN ('sales','eng','legal','finance','hr','exec','it')),
  role        text NOT NULL CHECK (role IN ('employee','manager','ceo','platform_admin','security_auditor','service')),
  title       text NOT NULL,
  manager_id  text REFERENCES core.users(user_id),
  clearance   smallint NOT NULL CHECK (clearance BETWEEN 1 AND 4),
  active      boolean NOT NULL DEFAULT true,
  persona_key text UNIQUE,                      -- "sales_staff", "finance_head", ... (demo + tests)
  country     text,                             -- ISO code of work location
  hired_on    date
);

-- Relationship groups: proj:<id>, matter:<id>, req:<id> (hiring panel), extra acl groups.
CREATE TABLE IF NOT EXISTS core.memberships (
  user_id text NOT NULL REFERENCES core.users(user_id),
  grp     text NOT NULL CHECK (position(',' in grp) = 0),
  PRIMARY KEY (user_id, grp)
);

-- Time-boxed access: legal viewing one CRM account by ticket, break-glass, delegation.
CREATE TABLE IF NOT EXISTS core.grants (
  grant_id   text PRIMARY KEY,
  user_id    text NOT NULL REFERENCES core.users(user_id),
  grp        text NOT NULL CHECK (position(',' in grp) = 0),
  reason     text NOT NULL,
  granted_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  break_glass boolean NOT NULL DEFAULT false
);

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.grants ENABLE ROW LEVEL SECURITY;
-- No policies for corpai_app and no grants: direct reads are impossible by design.
-- No FORCE: the owner (synth, and the SECURITY DEFINER function below) must bypass RLS.

-- Everything identity needs to build a Principal, in one call. Returns no row for unknown ids.
CREATE OR REPLACE FUNCTION core.principal_row(p_user_id text)
RETURNS TABLE (
  user_id text, name text, email text, dept text, role text, title text, manager_id text,
  clearance smallint, active boolean, persona_key text,
  memberships text[], grants text[], direct_reports text[]
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
  SELECT u.user_id, u.name, u.email, u.dept, u.role, u.title, u.manager_id,
         u.clearance, u.active, u.persona_key,
         coalesce((SELECT array_agg(m.grp ORDER BY m.grp) FROM core.memberships m WHERE m.user_id = u.user_id), '{}'),
         coalesce((SELECT array_agg(g.grp ORDER BY g.grp) FROM core.grants g
                   WHERE g.user_id = u.user_id AND g.revoked_at IS NULL AND g.expires_at > now()), '{}'),
         coalesce((SELECT array_agg(r.user_id ORDER BY r.user_id) FROM core.users r
                   WHERE r.manager_id = u.user_id AND r.active), '{}')
  FROM core.users u
  WHERE u.user_id = p_user_id
$$;

REVOKE ALL ON FUNCTION core.principal_row(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.principal_row(text) TO corpai_app;

-- 070 M3: the finance metric layer (design-review 二.8, decisions D7, architecture §5.1 "fin.metric").
--
-- metrics.snapshots holds one computed value per (metric, period, dims, version). The app role can only
-- SELECT through RLS. Rows are written by the owner job `svc:metrics` (corpai.metrics.refresh) and change
-- state only through the SECURITY DEFINER functions metrics.sign() and metrics.publish(), which check the
-- caller is the head of finance (role AND group AND dept AND clearance, so a forged app.role alone fails).
--
-- Label contract:
--   draft / signed  level = the metric's real level (L3/L4), acl = the finance ACL
--                   {dept:finance, head:finance, role:ceo} or a narrower subset chosen per metric
--   published       level = published_level (L2), acl = published_acl, both computed by the refresh job
--                   from config/metrics.yaml (company: {all}; dept: {dept:<d>, head:<d>, role:ceo,
--                   dept:finance}; management: CEO, every head and finance)
-- A signed or published row is frozen: its value, provenance and identity never change; a later refresh
-- that computes something different writes a new draft version instead.

CREATE TABLE IF NOT EXISTS metrics.snapshots (
  metric_id       text        NOT NULL CHECK (metric_id ~ '^[a-z][a-z0-9_]{1,63}$'),
  period          text        NOT NULL CHECK (period ~ '^[0-9]{4}-([0-9]{2}|Q[1-4])$'),
  dims            jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(dims) = 'object'),
  dims_key        text        NOT NULL DEFAULT '',          -- canonical "k=v&k=v" (sorted); '' = total
  version         int         NOT NULL DEFAULT 1 CHECK (version >= 1),
  value           numeric,                                    -- NULL when suppressed or undefined
  suppressed      boolean     NOT NULL DEFAULT false,         -- group below min_group (D7): never stored
  unit            text        NOT NULL CHECK (unit IN ('EUR', 'pct', 'count', 'days')),
  status          text        NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'signed', 'published')),
  computed_at     timestamptz NOT NULL DEFAULT now(),
  sql_sha256      text        NOT NULL CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
  sources         text[]      NOT NULL DEFAULT '{}',
  signed_by       text,
  signed_at       timestamptz,
  published_by    text,
  published_at    timestamptz,
  published_level smallint    NOT NULL CHECK (published_level BETWEEN 1 AND 4),
  published_acl   text[]      NOT NULL CHECK (cardinality(published_acl) > 0),
  level           smallint    NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept            text,
  owner_id        text,
  acl_groups      text[]      NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust           text        NOT NULL DEFAULT 'reviewed' CHECK (trust IN ('reviewed', 'internal', 'untrusted')),
  -- Aggregates only (D7): a metric row never names a data subject.
  subject_ids     text[]      NOT NULL DEFAULT '{}' CHECK (cardinality(subject_ids) = 0),
  PRIMARY KEY (metric_id, period, dims_key, version),
  CHECK (NOT suppressed OR value IS NULL),
  CHECK (status = 'draft' OR (signed_by IS NOT NULL AND signed_at IS NOT NULL)),
  CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS snapshots_lookup_idx ON metrics.snapshots (metric_id, period, dims_key, version DESC);
CREATE INDEX IF NOT EXISTS snapshots_acl_idx ON metrics.snapshots USING gin (acl_groups);

-- Integrity for every writer, the owner included (triggers fire for the owner; RLS does not):
-- rows are born as drafts, status only moves forward (draft -> signed -> published), and anything
-- signed is frozen, its label included: only the signed -> published step may change level and ACL,
-- and only to the published_level / published_acl fixed at refresh time (what the CFO signed off).
CREATE OR REPLACE FUNCTION metrics._guard_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' OR NEW.signed_by IS NOT NULL OR NEW.signed_at IS NOT NULL
       OR NEW.published_by IS NOT NULL OR NEW.published_at IS NOT NULL THEN
      RAISE EXCEPTION 'metric snapshots are inserted as drafts; sign and publish them through metrics.sign/publish'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'signed or published metric snapshots cannot be deleted' USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.metric_id IS DISTINCT FROM OLD.metric_id OR NEW.period IS DISTINCT FROM OLD.period
     OR NEW.dims_key IS DISTINCT FROM OLD.dims_key OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.dims IS DISTINCT FROM OLD.dims THEN
    RAISE EXCEPTION 'metric snapshot identity is immutable' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'draft' AND NEW.status = 'published' THEN
    RAISE EXCEPTION 'a metric snapshot must be signed before it is published' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'draft' AND (NEW.published_by IS NOT NULL OR NEW.published_at IS NOT NULL) THEN
    RAISE EXCEPTION 'a draft metric snapshot has no publication' USING ERRCODE = '42501';
  END IF;
  IF OLD.status <> 'draft' AND (
       NEW.value IS DISTINCT FROM OLD.value OR NEW.suppressed IS DISTINCT FROM OLD.suppressed
       OR NEW.unit IS DISTINCT FROM OLD.unit OR NEW.computed_at IS DISTINCT FROM OLD.computed_at
       OR NEW.sql_sha256 IS DISTINCT FROM OLD.sql_sha256 OR NEW.sources IS DISTINCT FROM OLD.sources
       OR NEW.signed_by IS DISTINCT FROM OLD.signed_by OR NEW.signed_at IS DISTINCT FROM OLD.signed_at
       OR NEW.published_level IS DISTINCT FROM OLD.published_level
       OR NEW.published_acl IS DISTINCT FROM OLD.published_acl
       OR NEW.dept IS DISTINCT FROM OLD.dept OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.trust IS DISTINCT FROM OLD.trust) THEN
    RAISE EXCEPTION 'a signed metric snapshot is frozen; refresh writes a new version' USING ERRCODE = '42501';
  END IF;
  IF OLD.status = 'signed' THEN
    IF NEW.status = 'draft' THEN
      RAISE EXCEPTION 'metric snapshot status only moves forward' USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'signed' AND (NEW.level IS DISTINCT FROM OLD.level OR NEW.acl_groups IS DISTINCT FROM OLD.acl_groups
                                  OR NEW.published_by IS NOT NULL OR NEW.published_at IS NOT NULL) THEN
      RAISE EXCEPTION 'a signed metric snapshot keeps its label until it is published' USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'published' AND (NEW.level IS DISTINCT FROM OLD.published_level
                                     OR NEW.acl_groups IS DISTINCT FROM OLD.published_acl) THEN
      RAISE EXCEPTION 'publishing sets exactly the published label fixed at refresh' USING ERRCODE = '42501';
    END IF;
  END IF;
  IF OLD.status = 'published' AND (NEW.status <> 'published' OR NEW.level IS DISTINCT FROM OLD.level
       OR NEW.acl_groups IS DISTINCT FROM OLD.acl_groups OR NEW.published_at IS DISTINCT FROM OLD.published_at
       OR NEW.published_by IS DISTINCT FROM OLD.published_by) THEN
    RAISE EXCEPTION 'a published metric snapshot is frozen' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS snapshots_guard ON metrics.snapshots;
CREATE TRIGGER snapshots_guard BEFORE INSERT OR UPDATE OR DELETE ON metrics.snapshots
  FOR EACH ROW EXECUTE FUNCTION metrics._guard_snapshot();

-- RLS: no FORCE (the refresh job and the SECURITY DEFINER functions run as the owner).
ALTER TABLE metrics.snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS snapshots_read ON metrics.snapshots;
CREATE POLICY snapshots_read ON metrics.snapshots FOR SELECT TO corpai_app
  USING (core.can_read(level, acl_groups));
GRANT SELECT ON metrics.snapshots TO corpai_app;

-- The caller is the active head of finance: role, dept and group must all agree, and a deactivated
-- principal (clearance 0) is refused even if the other variables were forged.
CREATE OR REPLACE FUNCTION metrics._caller_is_finance_head() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT core.cur_user() IS NOT NULL
     AND core.cur_clearance() > 0
     AND core.cur_role() = 'manager'
     AND core.cur_dept() = 'finance'
     AND 'head:finance' = ANY (core.cur_groups())
$$;
REVOKE ALL ON FUNCTION metrics._caller_is_finance_head() FROM PUBLIC;

-- Sign the latest draft of each requested metric for one period. Returns the rows it signed.
CREATE OR REPLACE FUNCTION metrics.sign(p_period text, p_metric_ids text[])
RETURNS TABLE (o_metric_id text, o_dims_key text, o_version int)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = metrics, pg_temp AS $$
BEGIN
  IF NOT metrics._caller_is_finance_head() THEN
    RAISE EXCEPTION 'only the head of finance may sign off metrics' USING ERRCODE = '42501';
  END IF;
  IF p_period IS NULL OR p_metric_ids IS NULL OR cardinality(p_metric_ids) = 0 THEN
    RAISE EXCEPTION 'period and metric ids are required' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    WITH latest AS (
      SELECT DISTINCT ON (s.metric_id, s.dims_key) s.metric_id, s.dims_key, s.version, s.status
      FROM metrics.snapshots s
      WHERE s.period = p_period AND s.metric_id = ANY (p_metric_ids)
      ORDER BY s.metric_id, s.dims_key, s.version DESC
    )
    UPDATE metrics.snapshots t
       SET status = 'signed', signed_by = core.cur_user(), signed_at = now()
      FROM latest l
     WHERE t.metric_id = l.metric_id AND t.period = p_period AND t.dims_key = l.dims_key
       AND t.version = l.version AND l.status = 'draft'
    RETURNING t.metric_id, t.dims_key, t.version;
END $$;

-- Publish the latest signed version: swap the label to the published audience computed at refresh.
CREATE OR REPLACE FUNCTION metrics.publish(p_period text, p_metric_ids text[])
RETURNS TABLE (o_metric_id text, o_dims_key text, o_version int)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = metrics, pg_temp AS $$
BEGIN
  IF NOT metrics._caller_is_finance_head() THEN
    RAISE EXCEPTION 'only the head of finance may publish metrics' USING ERRCODE = '42501';
  END IF;
  IF p_period IS NULL OR p_metric_ids IS NULL OR cardinality(p_metric_ids) = 0 THEN
    RAISE EXCEPTION 'period and metric ids are required' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    WITH latest AS (
      SELECT DISTINCT ON (s.metric_id, s.dims_key) s.metric_id, s.dims_key, s.version, s.status
      FROM metrics.snapshots s
      WHERE s.period = p_period AND s.metric_id = ANY (p_metric_ids)
      ORDER BY s.metric_id, s.dims_key, s.version DESC
    )
    UPDATE metrics.snapshots t
       SET status = 'published', published_by = core.cur_user(), published_at = now(),
           level = t.published_level, acl_groups = t.published_acl
      FROM latest l
     WHERE t.metric_id = l.metric_id AND t.period = p_period AND t.dims_key = l.dims_key
       AND t.version = l.version AND l.status = 'signed'
    RETURNING t.metric_id, t.dims_key, t.version;
END $$;

REVOKE ALL ON FUNCTION metrics.sign(text, text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION metrics.publish(text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION metrics.sign(text, text[]) TO corpai_app;
GRANT EXECUTE ON FUNCTION metrics.publish(text, text[]) TO corpai_app;

-- Which departments are under the D7 minimum group of five people (hired before p_end, as in the
-- opex metrics' n). Signals that show a per-dept amount containing payroll (dept_budget_overrun)
-- suppress those depts. Most readers cannot count hr.employees through RLS, so this SECURITY DEFINER
-- function answers only the boolean, never a count, and only to an active principal.
CREATE OR REPLACE FUNCTION metrics.dept_below_min_group(p_end date)
RETURNS TABLE (dept text, below boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = metrics, pg_temp AS $$
  WITH d AS (SELECT b.dept FROM fin.budgets b UNION SELECT e.dept FROM hr.employees e)
  SELECT d.dept,
         (SELECT count(*) FROM hr.employees e WHERE e.dept = d.dept AND e.hired_on < p_end) < 5
    FROM d
   WHERE d.dept IS NOT NULL AND core.cur_user() IS NOT NULL AND core.cur_clearance() > 0
$$;
REVOKE ALL ON FUNCTION metrics.dept_below_min_group(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION metrics.dept_below_min_group(date) TO corpai_app;

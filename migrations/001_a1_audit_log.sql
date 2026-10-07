-- 001 A1: append-only, hash-chained audit log (docs/architecture.md §9).
--
-- Trust model: corpai_app may only call audit.append() and audit.read_log(); it has no table
-- privileges at all. The chain is computed by a BEFORE INSERT trigger, so every insert (through
-- append() or directly by the owner) is chained under one advisory lock. UPDATE, DELETE and
-- TRUNCATE are rejected by triggers for everyone. A superuser or the table owner can still
-- disable triggers. The hash chain catches edits that leave the links broken; it cannot catch a
-- cut tail or a history re-hashed from some row onwards, because the owner can recompute every
-- hash (the canonical form is public). Those are caught by anchors: heads of the chain written
-- outside the database (corpai/audit/anchor.py), which audit.verify_chain(seqs, hashes) and
-- corpai.audit.verify_chain() check in addition to the links.

CREATE SEQUENCE IF NOT EXISTS audit.log_seq AS bigint;

CREATE TABLE IF NOT EXISTS audit.log (
  -- seq comes from audit.log_seq, but is assigned inside the chain trigger *after* the advisory
  -- lock is taken: a column default would be evaluated before the lock, and two concurrent
  -- writers could then commit in a different order than their seq numbers.
  seq         bigint PRIMARY KEY,
  ts          timestamptz NOT NULL,
  actor       text NOT NULL CHECK (actor <> ''),
  act_chain   text[] NOT NULL DEFAULT '{}'
              CHECK (array_position(act_chain, NULL) IS NULL AND coalesce(array_ndims(act_chain), 1) = 1),
  event       text NOT NULL CHECK (event IN (
                'login', 'demo_login', 'policy_decision', 'tool_call', 'retrieval', 'model_call',
                'route_decision', 'proposal_created', 'proposal_approved', 'proposal_rejected',
                'proposal_executed', 'export', 'outage_switch', 'budget_exceeded', 'break_glass',
                'owner_job')),
  action      text,
  resource    text,
  decision    text,
  reason      text,
  route       text CHECK (route IN ('local', 'external')),
  level       smallint CHECK (level BETWEEN 1 AND 4),
  approval_id text,
  run_id      text,
  details     jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
  prev_hash   bytea NOT NULL CHECK (octet_length(prev_hash) = 32),
  hash        bytea NOT NULL CHECK (octet_length(hash) = 32)
);

ALTER SEQUENCE audit.log_seq OWNED BY audit.log.seq;
CREATE INDEX IF NOT EXISTS log_run ON audit.log (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_event_ts ON audit.log (event, ts);

-- Owner-only data: RLS on with no policy for corpai_app, and no grants. Reads go through
-- audit.read_log() below.
ALTER TABLE audit.log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON audit.log FROM PUBLIC;
REVOKE ALL ON SEQUENCE audit.log_seq FROM PUBLIC;

-- ---- canonical serialisation ---------------------------------------------------------------
-- Not JSON: jsonb/json text output has its own key order and number formatting that another
-- implementation would have to imitate. Instead every field is length-prefixed, which is
-- unambiguous and trivially reproducible (corpai/audit/chain.py does the same):
--   NULL      -> 0x00
--   text t    -> 0x01 || int8 big-endian octet_length(utf8(t)) || utf8(t)
-- Integers and timestamps are encoded as decimal text (ts as microseconds since the Unix epoch,
-- independent of the session time zone); act_chain as its length followed by each element;
-- details as its jsonb text form (deterministic for a given jsonb value).

CREATE OR REPLACE FUNCTION audit._field(t text) RETURNS bytea
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT CASE WHEN t IS NULL THEN '\x00'::bytea
              ELSE '\x01'::bytea || int8send(octet_length(convert_to(t, 'UTF8'))::bigint)
                   || convert_to(t, 'UTF8') END
$$;

CREATE OR REPLACE FUNCTION audit.canonical(r audit.log) RETURNS bytea
LANGUAGE sql STABLE SET search_path = audit, pg_catalog, pg_temp AS $$
  SELECT convert_to('corpai.audit.v1', 'UTF8')
      || audit._field(r.seq::text)
      || audit._field(((extract(epoch FROM r.ts) * 1000000)::bigint)::text)
      || audit._field(r.actor)
      || audit._field(cardinality(r.act_chain)::text)
      || coalesce((SELECT string_agg(audit._field(x), ''::bytea ORDER BY o)
                   FROM unnest(r.act_chain) WITH ORDINALITY AS u(x, o)), ''::bytea)
      || audit._field(r.event)
      || audit._field(r.action)
      || audit._field(r.resource)
      || audit._field(r.decision)
      || audit._field(r.reason)
      || audit._field(r.route)
      || audit._field(r.level::text)
      || audit._field(r.approval_id)
      || audit._field(r.run_id)
      || audit._field(r.details::text)
$$;

-- ---- credential keys -----------------------------------------------------------------------
-- Defence in depth for rule 4 (tokens never reach the audit log or traces). The Python writers
-- check first with the same rule (corpai/audit/credentials.py): split camelCase, lower-case,
-- turn every non-alphanumeric run into '_', then look for a credential word between separators.
CREATE OR REPLACE FUNCTION audit.has_credential_key(j jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  WITH RECURSIVE walk(v) AS (
    SELECT j
    UNION ALL
    SELECT c.v FROM walk w,
      LATERAL (SELECT e.value AS v
                 FROM jsonb_each(CASE WHEN jsonb_typeof(w.v) = 'object' THEN w.v ELSE '{}'::jsonb END) e
               UNION ALL
               SELECT a.value
                 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.v) = 'array' THEN w.v ELSE '[]'::jsonb END) a
              ) c
  )
  SELECT EXISTS (
    SELECT 1 FROM walk w,
      jsonb_object_keys(CASE WHEN jsonb_typeof(w.v) = 'object' THEN w.v ELSE '{}'::jsonb END) k
    WHERE regexp_replace(lower(regexp_replace(k, '([a-z0-9])([A-Z])', '\1_\2', 'g')), '[^a-z0-9]+', '_', 'g')
          ~ '(^|_)(token|authorization|api_key|apikey|password|passwd|secret|cookie|session|bearer|jwt|credential|credentials|private_key)(_|$)'
  )
$$;

-- ---- chain trigger -------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit._chain_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = audit, pg_catalog, pg_temp AS $$
DECLARE
  last_hash bytea;
BEGIN
  -- Under REPEATABLE READ / SERIALIZABLE the "last row" read below would come from a snapshot
  -- taken before the lock, and two writers could chain onto the same predecessor.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'audit.log is appended only under READ COMMITTED'
      USING ERRCODE = 'invalid_transaction_state';
  END IF;
  PERFORM pg_advisory_xact_lock(7101, 1);  -- held until commit: the chain stays linear
  NEW.seq := nextval('audit.log_seq');
  NEW.ts := clock_timestamp();
  SELECT l.hash INTO last_hash FROM audit.log l ORDER BY l.seq DESC LIMIT 1;
  NEW.prev_hash := coalesce(last_hash, decode(repeat('00', 32), 'hex'));
  NEW.hash := sha256(NEW.prev_hash || audit.canonical(NEW));
  RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER log_chain BEFORE INSERT ON audit.log
  FOR EACH ROW EXECUTE FUNCTION audit._chain_insert();

CREATE OR REPLACE FUNCTION audit._reject_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  RAISE EXCEPTION 'audit.log is append-only: % is not allowed', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$;

-- Row trigger as specified, plus a statement trigger so even a zero-row UPDATE/DELETE fails loudly.
CREATE OR REPLACE TRIGGER log_no_update_delete BEFORE UPDATE OR DELETE ON audit.log
  FOR EACH ROW EXECUTE FUNCTION audit._reject_change();
CREATE OR REPLACE TRIGGER log_no_change_stmt BEFORE UPDATE OR DELETE ON audit.log
  FOR EACH STATEMENT EXECUTE FUNCTION audit._reject_change();
CREATE OR REPLACE TRIGGER log_no_truncate BEFORE TRUNCATE ON audit.log
  FOR EACH STATEMENT EXECUTE FUNCTION audit._reject_change();

-- ---- append ----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.append(
  p_event       text,
  p_actor       text,
  p_act_chain   text[]   DEFAULT '{}',
  p_action      text     DEFAULT NULL,
  p_resource    text     DEFAULT NULL,
  p_decision    text     DEFAULT NULL,
  p_reason      text     DEFAULT NULL,
  p_route       text     DEFAULT NULL,
  p_level       smallint DEFAULT NULL,
  p_approval_id text     DEFAULT NULL,
  p_run_id      text     DEFAULT NULL,
  p_details     jsonb    DEFAULT '{}'
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = audit, core, pg_temp AS $$
DECLARE
  v_seq bigint;
BEGIN
  IF audit.has_credential_key(coalesce(p_details, '{}'::jsonb)) THEN
    RAISE EXCEPTION 'audit details must not contain credential-like keys'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- seq, ts, prev_hash and hash are placeholders: the chain trigger overwrites them under the lock.
  INSERT INTO audit.log (seq, ts, actor, act_chain, event, action, resource, decision, reason,
                         route, level, approval_id, run_id, details, prev_hash, hash)
  VALUES (0, now(), p_actor, coalesce(p_act_chain, '{}'::text[]), p_event, p_action, p_resource,
          p_decision, p_reason, p_route, p_level, p_approval_id, p_run_id,
          coalesce(p_details, '{}'::jsonb), ''::bytea, ''::bytea)
  RETURNING seq INTO v_seq;
  RETURN v_seq;
END
$$;

-- ---- read (security auditor only) -------------------------------------------------------------
-- The database itself enforces who reads the log: the role must be security_auditor *and* the
-- principal's effective groups must contain role:security_auditor (deactivated principals get
-- no groups from principal_tx, so they are refused too).
CREATE OR REPLACE FUNCTION audit.read_log(after_seq bigint, lim int)
RETURNS SETOF audit.log
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = audit, core, pg_temp AS $$
BEGIN
  IF core.cur_role() IS DISTINCT FROM 'security_auditor'
     OR NOT ('role:security_auditor' = ANY (core.cur_groups())) THEN
    RAISE EXCEPTION 'only the security auditor may read the audit log'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN QUERY
    SELECT * FROM audit.log l
    WHERE l.seq > coalesce(after_seq, 0)
    ORDER BY l.seq
    LIMIT least(greatest(coalesce(lim, 200), 1), 1000);
END
$$;

-- ---- verification ------------------------------------------------------------------------------
-- Runs as the caller (the owner); corpai_app has no EXECUTE and could not read the table anyway.
-- p_anchor_seqs / p_anchor_hashes: the anchors (chain heads kept outside the database). Every
-- anchored seq must still exist with exactly that hash; otherwise first_bad_seq is one past the
-- last anchor that still matched (rows up to it are fixed by the links). Same rule as Python.
CREATE OR REPLACE FUNCTION audit.verify_chain(p_anchor_seqs bigint[] DEFAULT '{}',
                                              p_anchor_hashes bytea[] DEFAULT '{}',
                                              OUT ok boolean, OUT checked bigint, OUT first_bad_seq bigint)
LANGUAGE plpgsql STABLE SET search_path = audit, pg_catalog, pg_temp AS $$
DECLARE
  r audit.log;
  a record;
  h bytea;
  good bigint := 0;
  expected bytea := decode(repeat('00', 32), 'hex');
BEGIN
  ok := true;
  checked := 0;
  first_bad_seq := NULL;
  FOR r IN SELECT * FROM audit.log ORDER BY seq LOOP
    checked := checked + 1;
    IF first_bad_seq IS NULL
       AND (r.prev_hash IS DISTINCT FROM expected
            OR r.hash IS DISTINCT FROM sha256(r.prev_hash || audit.canonical(r))) THEN
      first_bad_seq := r.seq;
      ok := false;
    END IF;
    expected := r.hash;
  END LOOP;
  FOR a IN SELECT u.s, u.h
           FROM unnest(coalesce(p_anchor_seqs, '{}'::bigint[]), coalesce(p_anchor_hashes, '{}'::bytea[])) AS u(s, h)
           ORDER BY u.s, u.h LOOP
    h := NULL;
    SELECT l.hash INTO h FROM audit.log l WHERE l.seq = a.s;
    IF a.s IS NULL OR h IS NULL OR h IS DISTINCT FROM a.h THEN
      first_bad_seq := least(coalesce(first_bad_seq, good + 1), good + 1);
      ok := false;
      EXIT;
    END IF;
    good := a.s;
  END LOOP;
END
$$;

-- Functions are executable by PUBLIC by default; close that, then grant exactly two to the app.
REVOKE ALL ON FUNCTION audit._field(text), audit.canonical(audit.log), audit.has_credential_key(jsonb),
  audit._chain_insert(), audit._reject_change(),
  audit.append(text, text, text[], text, text, text, text, text, smallint, text, text, jsonb),
  audit.read_log(bigint, int), audit.verify_chain(bigint[], bytea[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  audit.append(text, text, text[], text, text, text, text, text, smallint, text, text, jsonb),
  audit.read_log(bigint, int) TO corpai_app;

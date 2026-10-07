-- 100 B3: conversation threads and the runs in them (architecture §12, phase-B spec).
--
-- LangGraph keeps a thread's state in schema lg (checkpoints, keyed by thread_id). Who owns a
-- thread lives here: the API refuses to continue a thread that is not the caller's, and resumes a
-- thread after an approval as its OWNER (fresh principal from the directory), never as the approver.
-- ops.thread_runs maps each run to its thread, so an approval of a proposal filed in run R finds
-- the waiting thread.

CREATE TABLE IF NOT EXISTS ops.threads (
  thread_id  text PRIMARY KEY CHECK (thread_id ~ '^thr_[0-9a-z]{20}$'),
  owner_id   text NOT NULL,
  agent      text NOT NULL CHECK (agent ~ '^[a-z][a-z0-9_]{0,63}$'),
  app        text NOT NULL CHECK (app ~ '^[a-z][a-z0-9_]{0,63}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS threads_owner_idx ON ops.threads (owner_id, created_at);

-- The owner reads their own threads; nobody else does (cur_clearance() > 0 keeps deactivated
-- principals and connections without a principal out). Writes only through ops.thread_create().
ALTER TABLE ops.threads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.threads FROM PUBLIC;
DROP POLICY IF EXISTS threads_own ON ops.threads;
CREATE POLICY threads_own ON ops.threads FOR SELECT TO corpai_app
  USING (owner_id = core.cur_user() AND core.cur_clearance() > 0);
GRANT SELECT ON ops.threads TO corpai_app;

CREATE TABLE IF NOT EXISTS ops.thread_runs (
  run_id     text PRIMARY KEY CHECK (run_id ~ '^run_[0-9a-z]{20}$'),
  thread_id  text NOT NULL REFERENCES ops.threads (thread_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS thread_runs_thread_idx ON ops.thread_runs (thread_id);
-- Owner-only: no policy, no grants. Reached through the functions below.
ALTER TABLE ops.thread_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.thread_runs FROM PUBLIC;

-- A new thread for the CURRENT principal: the owner is app.user_id, never a parameter.
CREATE OR REPLACE FUNCTION ops.thread_create(p_thread_id text, p_agent text, p_app text)
RETURNS ops.threads
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, core, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
  r ops.threads;
BEGIN
  IF v_user IS NULL OR v_user = '' OR core.cur_clearance() <= 0 OR v_user LIKE 'svc:%' THEN
    RAISE EXCEPTION 'only an active user may open a thread' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO ops.threads (thread_id, owner_id, agent, app) VALUES (p_thread_id, v_user, p_agent, p_app)
  RETURNING * INTO r;
  RETURN r;
END
$$;

-- Record that run p_run_id belongs to thread p_thread_id; only the thread's owner may add runs.
CREATE OR REPLACE FUNCTION ops.thread_run_add(p_run_id text, p_thread_id text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, core, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
BEGIN
  IF v_user IS NULL OR core.cur_clearance() <= 0
     OR NOT EXISTS (SELECT 1 FROM ops.threads t WHERE t.thread_id = p_thread_id AND t.owner_id = v_user) THEN
    RAISE EXCEPTION 'not your thread' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO ops.thread_runs (run_id, thread_id) VALUES (p_run_id, p_thread_id);
  RETURN true;
END
$$;

-- The thread waiting on a proposal, for whoever just decided it: its approver (after the
-- decision) or its requester (withdrawing). Anyone else gets no row. The thread must belong to
-- the requester, so a proposal cannot be used to reach someone else's thread.
CREATE OR REPLACE FUNCTION ops.thread_for_proposal(p_proposal_id text)
RETURNS TABLE (thread_id text, owner_id text, agent text, app text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = ops, core, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
BEGIN
  IF v_user IS NULL OR core.cur_clearance() <= 0 THEN
    RETURN;
  END IF;
  RETURN QUERY
    SELECT t.thread_id, t.owner_id, t.agent, t.app
    FROM ops.proposals p
    JOIN ops.thread_runs r ON r.run_id = p.run_id
    JOIN ops.threads t ON t.thread_id = r.thread_id AND t.owner_id = p.requester_id
    WHERE p.id = p_proposal_id
      AND ((p.approver_id = v_user AND p.status <> 'pending') OR p.requester_id = v_user);
END
$$;

-- Row filter of the LangGraph checkpoint tables (lg.*, created at runtime by the API lifespan,
-- corpai/agents/_checkpoint.py): a checkpoint row belongs to the thread's owner only. The
-- checkpointer runs every run on a connection carrying the run principal's session variables, so
-- the app role with no principal, or with anyone else's, sees and writes no conversation state.
CREATE OR REPLACE FUNCTION ops.thread_is_mine(p_thread_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ops, core, pg_temp AS $$
  SELECT coalesce(core.cur_user(), '') <> '' AND core.cur_user() NOT LIKE 'svc:%'
     AND core.cur_clearance() > 0
     AND EXISTS (SELECT 1 FROM ops.threads t WHERE t.thread_id = p_thread_id AND t.owner_id = core.cur_user())
$$;

-- A digest of every person name the classifier's gazetteer is built from (classify.load_gazetteer:
-- core.users without service accounts, crm.contacts, hr.candidates). The API compares it with the
-- digest it loaded before every run and reloads when synth or ingestion (other processes) changed
-- the names; a gazetteer from an older generation would let the new names through as L1. Only the
-- digest leaves the function, never a name.
CREATE OR REPLACE FUNCTION ops.name_source_fingerprint()
RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ops, core, pg_temp AS $$
  SELECT md5(coalesce(string_agg(n, chr(10) ORDER BY n), ''))
  FROM (SELECT u.name AS n FROM core.users u WHERE u.role <> 'service'
        UNION ALL SELECT c.name FROM crm.contacts c
        UNION ALL SELECT h.name FROM hr.candidates h) s
$$;

REVOKE ALL ON FUNCTION ops.thread_create(text, text, text), ops.thread_run_add(text, text),
  ops.thread_for_proposal(text), ops.thread_is_mine(text), ops.name_source_fingerprint() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.thread_create(text, text, text), ops.thread_run_add(text, text),
  ops.thread_for_proposal(text), ops.thread_is_mine(text), ops.name_source_fingerprint() TO corpai_app;

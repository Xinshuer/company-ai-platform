-- 060 B2: write proposals (architecture §11, design-review 二.12, decisions D8).
--
-- Every write an agent wants goes here first: "AI proposes -> a person approves -> the executor
-- runs it -> audit". The app role reads proposals through RLS (requester and possible approvers,
-- via the label columns) and changes them only through the SECURITY DEFINER functions below,
-- which take the caller's identity from the principal_tx session variables, never a parameter.
-- Executors (owner role, corpai/proposals/executors.py) are the only code that sets
-- executed / failed and writes the simulated systems.

CREATE TABLE IF NOT EXISTS ops.proposals (
  id           text PRIMARY KEY CHECK (id ~ '^prop_[0-9a-z]{20}$'),
  created_at   timestamptz NOT NULL DEFAULT now(),
  requester_id text NOT NULL,
  agent        text NOT NULL,
  run_id       text,
  thread_id    text,
  domain       text NOT NULL CHECK (domain IN ('crm', 'code', 'finance', 'hris', 'legal')),
  tool         text NOT NULL CHECK (tool ~ '^[a-z][a-z0-9_]{0,63}$'),
  tier         smallint NOT NULL CHECK (tier IN (2, 3)),
  params       jsonb NOT NULL,
  -- The exact canonical JSON that was hashed. Executors run from this text, not from the jsonb
  -- (jsonb may re-render numbers), and the CHECK below binds it to params_hash.
  params_text  text NOT NULL,
  params_hash  text NOT NULL CHECK (params_hash ~ '^[0-9a-f]{64}$'),
  summary      text NOT NULL,
  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'failed', 'expired')),
  approver_id  text,
  decided_at   timestamptz,
  reason       text,
  result       jsonb,
  executed_at  timestamptz,
  expires_at   timestamptz NOT NULL,
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed', 'internal', 'untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}',
  CHECK (params_hash = encode(sha256(convert_to(params_text, 'UTF8')), 'hex'))
);

CREATE INDEX IF NOT EXISTS proposals_requester_idx ON ops.proposals (requester_id, created_at);
CREATE INDEX IF NOT EXISTS proposals_pending_idx ON ops.proposals (domain, created_at) WHERE status = 'pending';

ALTER TABLE ops.proposals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.proposals FROM PUBLIC;
-- acl = {user:<requester>, head:<domain dept>, role:ceo}: exactly the requester and whoever may approve.
-- The requester always reads their own proposal, whatever level the classifier gave it: they wrote
-- it in their own session, and must be able to follow and withdraw it (an L3 rep's mail whose
-- wording classifies L4 would otherwise vanish from "mine" while their head can still approve it).
DROP POLICY IF EXISTS proposals_read ON ops.proposals;
CREATE POLICY proposals_read ON ops.proposals FOR SELECT TO corpai_app
  USING ((requester_id = core.cur_user() AND core.cur_clearance() > 0) OR core.can_read(level, acl_groups));
GRANT SELECT ON ops.proposals TO corpai_app;

-- Domain -> owning department (policy.catalogue.DOMAIN_DEPT); NULL for read-only domains.
CREATE OR REPLACE FUNCTION ops.proposal_domain_dept(p_domain text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT CASE p_domain WHEN 'crm' THEN 'sales' WHEN 'code' THEN 'eng' WHEN 'finance' THEN 'finance'
                       WHEN 'hris' THEN 'hr' WHEN 'legal' THEN 'legal' ELSE NULL END
$$;

-- The write-tool catalogue with tiers (policy.catalogue.TOOL_CATALOGUE; a test keeps the two equal).
-- NULL for anything that is not a write tool. The SQL functions below use it so that a caller
-- claiming a lower tier cannot turn a four-eyes action into one the requester may confirm.
CREATE OR REPLACE FUNCTION ops.tool_tier(p_domain text, p_tool text) RETURNS smallint
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT (CASE p_domain || '.' || p_tool
    WHEN 'crm.log_interaction' THEN 2 WHEN 'crm.update_opportunity_stage' THEN 2 WHEN 'crm.send_email' THEN 3
    WHEN 'code.create_ticket' THEN 2 WHEN 'code.comment_ticket' THEN 2
    WHEN 'finance.post_journal_entry' THEN 3
    WHEN 'hris.create_requisition' THEN 2
    WHEN 'legal.create_contract_draft' THEN 2 WHEN 'legal.send_contract' THEN 3
    ELSE NULL END)::smallint
$$;

-- Insert a pending proposal for the CURRENT principal. The requester, the ACL and the expiry are
-- computed here, so request-path code cannot file a proposal in someone else's name or widen
-- who may read it. Level comes from Python (classify_text), checked; the stored tier is
-- max(claimed, catalogue tier) computed here, and a tool outside the write catalogue is refused.
CREATE OR REPLACE FUNCTION ops.proposal_create(
  p_id text, p_agent text, p_run_id text, p_thread_id text, p_domain text, p_tool text, p_tier smallint,
  p_params_text text, p_params_hash text, p_summary text, p_level smallint, p_subject_ids text[])
RETURNS SETOF ops.proposals
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
  v_dept text := ops.proposal_domain_dept(p_domain);
  v_cat smallint := ops.tool_tier(p_domain, p_tool);
BEGIN
  IF v_user IS NULL OR v_user LIKE 'svc:%' OR core.cur_clearance() <= 0
     OR NOT (('user:' || v_user) = ANY (core.cur_groups())) THEN
    RAISE EXCEPTION 'only an active user may file a proposal' USING ERRCODE = '42501';
  END IF;
  IF v_dept IS NULL OR v_cat IS NULL THEN
    RAISE EXCEPTION 'not a write tool' USING ERRCODE = '22023';
  END IF;
  IF p_level IS NULL OR p_level NOT BETWEEN 1 AND 4 OR p_tier IS NULL OR p_tier NOT IN (2, 3)
     OR p_summary IS NULL OR length(btrim(p_summary)) = 0 OR length(p_summary) > 2000 THEN
    RAISE EXCEPTION 'invalid proposal' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
    INSERT INTO ops.proposals (id, requester_id, agent, run_id, thread_id, domain, tool, tier, params, params_text,
                               params_hash, summary, expires_at, level, dept, owner_id, acl_groups, subject_ids)
    VALUES (p_id, v_user, p_agent, nullif(p_run_id, ''), nullif(p_thread_id, ''), p_domain, p_tool,
            greatest(p_tier, v_cat),
            p_params_text::jsonb, p_params_text, p_params_hash, p_summary,
            now() + interval '7 days',  -- wall clock: approvals are a security control (§3 two clocks)
            p_level, v_dept, v_user, ARRAY['user:' || v_user, 'head:' || v_dept, 'role:ceo'],
            coalesce(p_subject_ids, '{}'))
    RETURNING *;
END
$$;

-- Approve or reject a pending proposal as the CURRENT principal. Mirrors policy.can_approve():
-- tier 2 = the requester, the head of the domain's department or the CEO; tier 3 = four-eyes
-- (never the requester) by that head or the CEO. Rejecting is also open to the requester
-- (withdrawing a request). Approval must quote the exact params_hash the approver was shown.
-- The tier applied is max(stored tier, catalogue tier). An expired proposal, or one whose requester
-- has been deactivated since filing it (deactivation takes effect at once, §5: nothing runs in
-- their name afterwards), is marked 'expired' and returned unchanged otherwise; the caller raises.
CREATE OR REPLACE FUNCTION ops.proposal_decide(p_id text, p_decision text, p_params_hash text, p_reason text)
RETURNS SETOF ops.proposals
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
  r ops.proposals;
  v_requester boolean;
  v_ceo boolean;
  v_head boolean;
  v_tier smallint;
BEGIN
  IF v_user IS NULL OR v_user LIKE 'svc:%' OR core.cur_clearance() <= 0
     OR NOT (('user:' || v_user) = ANY (core.cur_groups())) THEN
    RAISE EXCEPTION 'only an active user may decide on a proposal' USING ERRCODE = '42501';
  END IF;
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RAISE EXCEPTION 'decision must be approved or rejected' USING ERRCODE = '22023';
  END IF;
  IF p_reason IS NULL OR length(btrim(p_reason)) = 0 OR length(p_reason) > 2000 THEN
    RAISE EXCEPTION 'a decision needs a reason (max 2000 chars)' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO r FROM ops.proposals x WHERE x.id = p_id FOR UPDATE;
  -- Not readable counts as not found: no hint that the proposal exists.
  IF NOT FOUND OR NOT (core.can_read(r.level, r.acl_groups) OR r.requester_id = v_user) THEN
    RAISE EXCEPTION 'proposal not found' USING ERRCODE = 'P0002';
  END IF;
  IF r.status <> 'pending' THEN
    RAISE EXCEPTION 'proposal is not pending' USING ERRCODE = '55000';
  END IF;
  IF now() > r.expires_at THEN
    UPDATE ops.proposals x SET status = 'expired', decided_at = now(), reason = 'expired before a decision'
    WHERE x.id = p_id;
    RETURN QUERY SELECT * FROM ops.proposals x WHERE x.id = p_id;
    RETURN;
  END IF;
  IF p_decision = 'approved'
     AND NOT EXISTS (SELECT 1 FROM core.users u WHERE u.user_id = r.requester_id AND u.active) THEN
    UPDATE ops.proposals x SET status = 'expired', decided_at = now(), reason = 'requester no longer active'
    WHERE x.id = p_id;
    RETURN QUERY SELECT * FROM ops.proposals x WHERE x.id = p_id;
    RETURN;
  END IF;

  v_tier := greatest(r.tier, coalesce(ops.tool_tier(r.domain, r.tool), 3::smallint));
  v_requester := r.requester_id = v_user;
  v_ceo := core.cur_role() = 'ceo' AND 'role:ceo' = ANY (core.cur_groups());
  v_head := core.cur_role() = 'manager' AND core.cur_dept() = r.dept
            AND ('head:' || r.dept) = ANY (core.cur_groups());

  IF p_decision = 'approved' THEN
    IF p_params_hash IS DISTINCT FROM r.params_hash THEN
      RAISE EXCEPTION 'params hash does not match' USING ERRCODE = '22023';
    END IF;
    IF NOT ((v_tier = 2 AND (v_requester OR v_head OR v_ceo))
            OR (v_tier = 3 AND NOT v_requester AND (v_head OR v_ceo))) THEN
      RAISE EXCEPTION 'not allowed to approve this proposal' USING ERRCODE = '42501';
    END IF;
  ELSE
    IF NOT (v_requester OR v_head OR v_ceo) THEN
      RAISE EXCEPTION 'not allowed to reject this proposal' USING ERRCODE = '42501';
    END IF;
  END IF;

  RETURN QUERY
    UPDATE ops.proposals x SET status = p_decision, approver_id = v_user, decided_at = now(), reason = p_reason
    WHERE x.id = p_id
    RETURNING *;
END
$$;

REVOKE ALL ON FUNCTION ops.proposal_domain_dept(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.tool_tier(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.proposal_create(text, text, text, text, text, text, smallint, text, text, text, smallint,
                                           text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.proposal_decide(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.proposal_create(text, text, text, text, text, text, smallint, text, text, text, smallint,
                                              text[]) TO corpai_app;
GRANT EXECUTE ON FUNCTION ops.proposal_decide(text, text, text, text) TO corpai_app;

-- 090 M4 HR screening (decision D6; GDPR Art. 22; EU AI Act Annex III 4(a)).
-- hr.screening_evidence: what the model extracted per hard requirement (met / not met / unknown + quote).
-- No score, no ranking, no decision lives here. Decisions are a person's and go to hr.screenings
-- (synth table, migration 044) through hr.record_screening_decision(), with a required reason.
-- Both writers are SECURITY DEFINER: they take the actor from core.cur_user(), never from a parameter.

CREATE TABLE IF NOT EXISTS hr.screening_evidence (
  evidence_id  text PRIMARY KEY CHECK (evidence_id ~ '^evd_[0-9a-z]{20}$'),
  candidate_id text NOT NULL REFERENCES hr.candidates(candidate_id),
  req_id       text NOT NULL REFERENCES hr.requisitions(req_id),
  created_by   text NOT NULL,
  evidence     jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
  -- Résumés are L3 and screening results L4: the model call must have run on the local route.
  model_route  text NOT NULL CHECK (model_route = 'local'),
  created_at   timestamptz NOT NULL DEFAULT now(),
  level        smallint NOT NULL DEFAULT 4 CHECK (level = 4),
  dept         text NOT NULL DEFAULT 'hr',
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}',
  -- D6: evidence only. A score, rank or recommendation key is refused even if a writer tried.
  CHECK (NOT (evidence ?| ARRAY['score', 'total_score', 'rank', 'ranking', 'recommendation', 'decision',
                                'reject', 'rejected']))
);

CREATE INDEX IF NOT EXISTS screening_evidence_candidate_idx ON hr.screening_evidence (candidate_id, created_at);

ALTER TABLE hr.screening_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS screening_evidence_read ON hr.screening_evidence;
CREATE POLICY screening_evidence_read ON hr.screening_evidence FOR SELECT TO corpai_app
  USING (core.can_read(level, acl_groups));
GRANT SELECT ON hr.screening_evidence TO corpai_app;

-- Who may screen or decide on a candidate: HR staff on that requisition (req:<id>) or the head of HR.
-- Role AND group AND dept are all checked, so a forged app.role alone gets nowhere (architecture §4).
CREATE OR REPLACE FUNCTION hr._may_screen(p_req_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = hr, core, pg_temp AS $$
  SELECT core.cur_user() IS NOT NULL
     AND core.cur_clearance() >= 4
     AND core.cur_dept() = 'hr'
     AND (
          (core.cur_role() = 'manager' AND 'head:hr' = ANY (core.cur_groups()))
       OR (core.cur_role() IN ('employee', 'manager') AND 'dept:hr' = ANY (core.cur_groups())
           AND ('req:' || p_req_id) = ANY (core.cur_groups()))
     )
$$;

CREATE OR REPLACE FUNCTION hr.write_screening_evidence(p_evidence_id text, p_candidate_id text,
                                                       p_evidence jsonb, p_model_route text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = hr, core, pg_temp AS $$
DECLARE
  v_req text;
  v_level smallint;
  v_acl text[];
BEGIN
  SELECT c.req_id, c.level, c.acl_groups INTO v_req, v_level, v_acl FROM hr.candidates c
   WHERE c.candidate_id = p_candidate_id;
  -- The caller must be able to read the candidate under the normal RLS rule as well.
  IF v_req IS NULL OR NOT core.can_read(v_level, v_acl) OR NOT hr._may_screen(v_req) THEN
    RAISE EXCEPTION 'no permission to screen this candidate' USING ERRCODE = '42501';
  END IF;
  INSERT INTO hr.screening_evidence (evidence_id, candidate_id, req_id, created_by, evidence, model_route,
                                     level, dept, owner_id, acl_groups, trust, subject_ids)
  VALUES (p_evidence_id, p_candidate_id, v_req, core.cur_user(), p_evidence, p_model_route,
          4, 'hr', core.cur_user(), ARRAY['req:' || v_req, 'head:hr'], 'internal', ARRAY[p_candidate_id]);
  RETURN p_evidence_id;
END
$$;

-- A human decision (advance / reject / hold) with a reason; never exposed as a model tool.
CREATE OR REPLACE FUNCTION hr.record_screening_decision(p_screening_id text, p_candidate_id text,
                                                        p_decision text, p_reason text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = hr, core, pg_temp AS $$
DECLARE
  v_req text;
  v_level smallint;
  v_acl text[];
  v_items jsonb;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('advance', 'reject', 'hold') THEN
    RAISE EXCEPTION 'decision must be advance, reject or hold' USING ERRCODE = '22023';
  END IF;
  IF p_reason IS NULL OR length(btrim(p_reason)) = 0 OR length(p_reason) > 2000 THEN
    RAISE EXCEPTION 'a decision needs a reason (1-2000 characters)' USING ERRCODE = '22023';
  END IF;
  IF p_screening_id IS NULL OR p_screening_id !~ '^scr_[0-9a-z]{20}$' THEN
    RAISE EXCEPTION 'malformed screening id' USING ERRCODE = '22023';
  END IF;
  SELECT c.req_id, c.level, c.acl_groups INTO v_req, v_level, v_acl FROM hr.candidates c
   WHERE c.candidate_id = p_candidate_id;
  IF v_req IS NULL OR NOT core.can_read(v_level, v_acl) OR NOT hr._may_screen(v_req) THEN
    RAISE EXCEPTION 'no permission to decide on this candidate' USING ERRCODE = '42501';
  END IF;
  -- The decision row keeps the evidence the person saw (latest extraction), for Art. 22 review.
  SELECT e.evidence -> 'items' INTO v_items FROM hr.screening_evidence e
   WHERE e.candidate_id = p_candidate_id ORDER BY e.created_at DESC, e.evidence_id DESC LIMIT 1;
  INSERT INTO hr.screenings (screening_id, candidate_id, req_id, created_by, evidence, decision, reason,
                             decided_by, decided_at, level, dept, owner_id, acl_groups, trust, subject_ids)
  VALUES (p_screening_id, p_candidate_id, v_req, core.cur_user(), coalesce(v_items, '[]'::jsonb), p_decision,
          btrim(p_reason), core.cur_user(), now(), 4, 'hr', core.cur_user(), ARRAY['req:' || v_req, 'head:hr'],
          'internal', ARRAY[p_candidate_id]);
  RETURN p_screening_id;
END
$$;

REVOKE ALL ON FUNCTION hr._may_screen(text), hr.write_screening_evidence(text, text, jsonb, text),
  hr.record_screening_decision(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hr.write_screening_evidence(text, text, jsonb, text),
  hr.record_screening_decision(text, text, text, text) TO corpai_app;

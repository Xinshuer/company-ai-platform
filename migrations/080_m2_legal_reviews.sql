-- 080 M2: stored contract reviews (architecture §16 M2; design-review 二.6, 三 法务).
--
-- A review quotes the contract it reviewed, so it is at least L3 and at least the contract's own
-- level (a canary contract is L4). Readers: the requester, the head of legal and the contract's
-- handling lawyer. The app role cannot INSERT/UPDATE/DELETE: the only write path is
-- legal.save_review(), which takes the requester from the principal_tx session (core.cur_user()),
-- never from a parameter, derives handler and level from the contract row, and refuses callers who
-- cannot read that contract. Reviews are immutable once written.
-- No foreign key to legal.contracts on purpose: synth truncates legal.* with CASCADE on every run.

CREATE TABLE IF NOT EXISTS legal.reviews (
  review_id    text PRIMARY KEY CHECK (review_id ~ '^rev_[0-9a-z]{20}$'),
  contract_id  text CHECK (contract_id IS NULL OR contract_id ~ '^C-[0-9]{4}$'),
  requested_by text NOT NULL,
  run_id       text,
  playbook_ref text NOT NULL CHECK (length(playbook_ref) BETWEEN 3 AND 200),
  result       jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  level        smallint NOT NULL CHECK (level BETWEEN 3 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS reviews_contract_idx ON legal.reviews (contract_id, created_at DESC);
CREATE INDEX IF NOT EXISTS reviews_acl_idx ON legal.reviews USING gin (acl_groups);

ALTER TABLE legal.reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reviews_read ON legal.reviews;
CREATE POLICY reviews_read ON legal.reviews FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
GRANT SELECT ON legal.reviews TO corpai_app;

CREATE OR REPLACE FUNCTION legal.save_review(p_review_id text, p_contract_id text, p_playbook_ref text,
                                             p_result jsonb, p_level smallint, p_run_id text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = legal, core, pg_temp AS $$
DECLARE
  v_user    text := core.cur_user();
  v_groups  text[] := core.cur_groups();
  v_level   smallint;
  v_handler text;
  v_trust   text := 'untrusted';
  v_acl     text[];
  c         record;
BEGIN
  -- Identity comes from the session; a deactivated principal has clearance 0 and no groups.
  IF v_user IS NULL OR core.cur_clearance() <= 0 THEN
    RAISE EXCEPTION 'no active principal' USING ERRCODE = '42501';
  END IF;
  -- Only the legal department and the CEO run contract reviews (agent legal_review).
  IF NOT (v_groups && ARRAY['dept:legal', 'head:legal', 'role:ceo']) THEN
    RAISE EXCEPTION 'only legal and the CEO may store contract reviews' USING ERRCODE = '42501';
  END IF;
  IF p_level IS NULL OR p_level NOT BETWEEN 1 AND 4 THEN
    RAISE EXCEPTION 'invalid level' USING ERRCODE = '22023';
  END IF;
  IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object' OR pg_column_size(p_result) > 2000000 THEN
    RAISE EXCEPTION 'invalid review result' USING ERRCODE = '22023';
  END IF;
  v_level := greatest(3, p_level);
  IF p_contract_id IS NOT NULL THEN
    SELECT level, acl_groups, handler_id, trust, encode(sha256(convert_to(text, 'UTF8')), 'hex') AS text_sha256
      INTO c FROM legal.contracts WHERE contract_id = p_contract_id;
    -- SECURITY DEFINER bypasses RLS, so the read check is explicit: no review of a contract the
    -- caller cannot see (and no probing which contract ids exist).
    IF NOT FOUND OR NOT core.can_read(c.level, c.acl_groups) THEN
      RAISE EXCEPTION 'no access to this contract' USING ERRCODE = '42501';
    END IF;
    v_level := greatest(v_level, c.level);
    v_handler := c.handler_id;
    -- 'internal' only when the reviewed text is byte-identical to a non-untrusted stored contract
    -- (text_sha256 is computed by review code from the text it reviewed). A counterparty's
    -- marked-up copy of a signed contract is uploaded text (ENGINEERING rule 8): it stays
    -- 'untrusted', because the stored review quotes it verbatim, injected paragraphs included.
    v_trust := CASE WHEN c.trust <> 'untrusted' AND p_result->>'text_sha256' = c.text_sha256
                    THEN 'internal' ELSE 'untrusted' END;
  END IF;
  v_acl := ARRAY['user:' || v_user, 'head:legal'];
  IF v_handler IS NOT NULL AND v_handler <> v_user THEN
    v_acl := v_acl || ('user:' || v_handler);
  END IF;
  INSERT INTO legal.reviews (review_id, contract_id, requested_by, run_id, playbook_ref, result, level, dept,
                             owner_id, acl_groups, trust)
  VALUES (p_review_id, p_contract_id, v_user, nullif(p_run_id, ''), p_playbook_ref, p_result, v_level, 'legal',
          v_user, v_acl, v_trust);
  RETURN p_review_id;
END
$$;

REVOKE ALL ON FUNCTION legal.save_review(text, text, text, jsonb, smallint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION legal.save_review(text, text, text, jsonb, smallint, text) TO corpai_app;

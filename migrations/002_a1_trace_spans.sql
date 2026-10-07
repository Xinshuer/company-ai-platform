-- 002 A1: trace spans (docs/architecture.md §9). Traces are "the second database" that design
-- review gap 11 warns about, so access is narrower than for business rows:
--   requester        reads their own spans (RLS below), text already masked at write time
--   platform_admin   trace.read_meta(): one run's metadata, never input/output text
--   heads / CEO      trace.dept_stats(): aggregate counts only, never text or per-span rows
-- corpai_app never writes the table directly; trace.write_span() is the only write path.

CREATE TABLE IF NOT EXISTS trace.spans (
  span_id     text PRIMARY KEY,
  run_id      text NOT NULL,
  parent_id   text,
  name        text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('node', 'llm', 'tool', 'retrieval', 'policy')),
  user_id     text NOT NULL,
  dept        text,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  started_at  timestamptz NOT NULL,
  ended_at    timestamptz,
  status      text NOT NULL CHECK (status IN ('running', 'ok', 'error')),
  attrs       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(attrs) = 'object'),
  input_text  text,
  output_text text,
  -- L4 spans keep metadata only (design review §4): enforced here, not just in the tracer.
  CONSTRAINT spans_l4_metadata_only CHECK (level < 4 OR (input_text IS NULL AND output_text IS NULL)),
  CONSTRAINT spans_end_consistent CHECK ((status = 'running') = (ended_at IS NULL))
);

CREATE INDEX IF NOT EXISTS spans_run ON trace.spans (run_id, started_at);
CREATE INDEX IF NOT EXISTS spans_user ON trace.spans (user_id, started_at);
CREATE INDEX IF NOT EXISTS spans_dept ON trace.spans (dept, started_at);

ALTER TABLE trace.spans ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON trace.spans FROM PUBLIC;
DROP POLICY IF EXISTS spans_own ON trace.spans;
-- Own runs only. cur_clearance() > 0 keeps deactivated principals (principal_tx gives them
-- clearance 0) and connections without a principal out.
CREATE POLICY spans_own ON trace.spans FOR SELECT TO corpai_app
  USING (user_id = core.cur_user() AND core.cur_clearance() > 0);
GRANT SELECT ON trace.spans TO corpai_app;

-- Insert at span start (no text yet: the final level is unknown), update once at span end.
-- The span's owner is the session principal (app.user_id / app.dept), not a parameter.
CREATE OR REPLACE FUNCTION trace.write_span(
  p_span_id     text,
  p_run_id      text,
  p_parent_id   text,
  p_name        text,
  p_kind        text,
  p_level       smallint,
  p_started_at  timestamptz,
  p_ended_at    timestamptz,
  p_status      text,
  p_attrs       jsonb,
  p_input_text  text,
  p_output_text text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = trace, core, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
  cur trace.spans;
BEGIN
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'trace.write_span needs a principal (app.user_id)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF audit.has_credential_key(coalesce(p_attrs, '{}'::jsonb)) THEN
    RAISE EXCEPTION 'span attrs must not contain credential-like keys'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO cur FROM trace.spans WHERE span_id = p_span_id FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO trace.spans (span_id, run_id, parent_id, name, kind, user_id, dept, level,
                             started_at, ended_at, status, attrs, input_text, output_text)
    VALUES (p_span_id, p_run_id, p_parent_id, p_name, p_kind, v_user, core.cur_dept(), p_level,
            p_started_at, p_ended_at, p_status, coalesce(p_attrs, '{}'::jsonb), p_input_text, p_output_text);
    RETURN;
  END IF;

  -- A span is written once more at its end and then frozen. Its identity and owner never change,
  -- and its level only goes up, so text masked for a higher level is never relabelled lower.
  IF cur.status <> 'running' THEN
    RAISE EXCEPTION 'span % has already ended', p_span_id USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF cur.user_id <> v_user OR cur.run_id <> p_run_id OR cur.kind <> p_kind
     OR cur.name <> p_name OR cur.parent_id IS DISTINCT FROM p_parent_id THEN
    RAISE EXCEPTION 'span % identity or owner cannot change', p_span_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_level < cur.level THEN
    RAISE EXCEPTION 'span level may only go up (% -> %)', cur.level, p_level
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE trace.spans
     SET level = p_level, ended_at = p_ended_at, status = p_status,
         attrs = coalesce(p_attrs, '{}'::jsonb), input_text = p_input_text, output_text = p_output_text
   WHERE span_id = p_span_id;
END
$$;

-- Platform admins run the system: they see what ran, when, for whom and how it ended, but no text.
CREATE OR REPLACE FUNCTION trace.read_meta(p_run_id text)
RETURNS TABLE (span_id text, run_id text, parent_id text, name text, kind text, user_id text,
               dept text, level smallint, started_at timestamptz, ended_at timestamptz,
               status text, attrs jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = trace, core, pg_temp AS $$
BEGIN
  IF core.cur_role() IS DISTINCT FROM 'platform_admin'
     OR NOT ('role:platform_admin' = ANY (core.cur_groups())) THEN
    RAISE EXCEPTION 'only the platform admin may read trace metadata'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN QUERY
    SELECT s.span_id, s.run_id, s.parent_id, s.name, s.kind, s.user_id, s.dept, s.level,
           s.started_at, s.ended_at, s.status, s.attrs
    FROM trace.spans s
    WHERE s.run_id = p_run_id
    ORDER BY s.started_at, s.span_id;
END
$$;

-- Heads see aggregate counts for their own department only; never text, never rows. This is
-- decide()'s trace-head-aggregate rule (head:<dept> of the run's department): the CEO is head of
-- exec and sees exec's counts, nothing company-wide (design-review 五, trace 内容: CEO 否).
CREATE OR REPLACE FUNCTION trace.dept_stats(since timestamptz)
RETURNS TABLE (dept text, kind text, route text, level smallint, status text, n bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = trace, core, pg_temp AS $$
BEGIN
  IF core.cur_role() IS NULL OR core.cur_role() NOT IN ('manager', 'ceo') OR core.cur_dept() IS NULL
     OR NOT (('head:' || core.cur_dept()) = ANY (core.cur_groups())) THEN
    RAISE EXCEPTION 'only department heads may read trace statistics, for their own department'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN QUERY
    SELECT s.dept, s.kind, s.attrs ->> 'route', s.level, s.status, count(*)
    FROM trace.spans s
    WHERE s.started_at >= coalesce(since, '-infinity'::timestamptz)
      AND s.dept = core.cur_dept()
    GROUP BY 1, 2, 3, 4, 5
    ORDER BY 1, 2, 3, 4, 5;
END
$$;

REVOKE ALL ON FUNCTION
  trace.write_span(text, text, text, text, text, smallint, timestamptz, timestamptz, text, jsonb, text, text),
  trace.read_meta(text), trace.dept_stats(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  trace.write_span(text, text, text, text, text, smallint, timestamptz, timestamptz, text, jsonb, text, text),
  trace.read_meta(text), trace.dept_stats(timestamptz) TO corpai_app;

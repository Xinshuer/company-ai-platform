-- 103 wrap-up: GDPR data subjects on trace spans (architecture §9; issues-log D-04).
-- Business rows carry subject_ids (the people a row is about: employee, contact and candidate ids) so
-- that an erasure request can find them. Spans keep business text (masked by level), so they need the
-- same handle: a tool or retrieval span records the subject ids of the rows and documents it read, and
-- trace.erase_subject() blanks the text of every span of every run in the threads that touched that
-- person (a later turn's model call can quote an earlier turn's data). The ids are erasure metadata
-- only: read_run, read_meta and dept_stats never return them.

ALTER TABLE trace.spans ADD COLUMN IF NOT EXISTS subject_ids text[] NOT NULL DEFAULT '{}';
ALTER TABLE trace.spans DROP CONSTRAINT IF EXISTS spans_subject_ids_bounded;
ALTER TABLE trace.spans ADD CONSTRAINT spans_subject_ids_bounded CHECK (cardinality(subject_ids) <= 500);
CREATE INDEX IF NOT EXISTS spans_subjects ON trace.spans USING gin (subject_ids);

-- write_span (migration 002) with one more argument. The old signature is dropped rather than
-- overloaded, so there is still exactly one write path; callers that pass twelve arguments get the
-- default (no subjects). Subjects, like the level, only accumulate: the end write adds to the start's.
DROP FUNCTION IF EXISTS
  trace.write_span(text, text, text, text, text, smallint, timestamptz, timestamptz, text, jsonb, text, text);

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
  p_output_text text,
  p_subject_ids text[] DEFAULT '{}'
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = trace, core, pg_temp AS $$
DECLARE
  v_user text := core.cur_user();
  v_subjects text[] := ARRAY(SELECT DISTINCT s FROM unnest(coalesce(p_subject_ids, '{}'::text[])) AS s
                             WHERE s IS NOT NULL ORDER BY 1);
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
  -- ids, not text: a subject id is a row key such as u0123, CT-0042 or CAND-007
  IF EXISTS (SELECT 1 FROM unnest(v_subjects) AS s WHERE s !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,63}$') THEN
    RAISE EXCEPTION 'span subject_ids must be row ids' USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT * INTO cur FROM trace.spans WHERE span_id = p_span_id FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO trace.spans (span_id, run_id, parent_id, name, kind, user_id, dept, level,
                             started_at, ended_at, status, attrs, input_text, output_text, subject_ids)
    VALUES (p_span_id, p_run_id, p_parent_id, p_name, p_kind, v_user, core.cur_dept(), p_level,
            p_started_at, p_ended_at, p_status, coalesce(p_attrs, '{}'::jsonb), p_input_text, p_output_text,
            v_subjects);
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
         attrs = coalesce(p_attrs, '{}'::jsonb), input_text = p_input_text, output_text = p_output_text,
         subject_ids = ARRAY(SELECT DISTINCT s FROM unnest(cur.subject_ids || v_subjects) AS s ORDER BY 1)
   WHERE span_id = p_span_id;
END
$$;

REVOKE ALL ON FUNCTION
  trace.write_span(text, text, text, text, text, smallint, timestamptz, timestamptz, text, jsonb, text, text,
                   text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  trace.write_span(text, text, text, text, text, smallint, timestamptz, timestamptz, text, jsonb, text, text,
                   text[]) TO corpai_app;

-- Erasure (GDPR Art. 17), run by the owner as part of a data subject request: every span of every run
-- in a thread where some span read the subject loses its input and output text (metadata stays: who
-- ran what, when, at which level) and the subject id itself. Runs outside a thread count on their own.
-- Returns the number of spans changed. Owner only: corpai_app has no EXECUTE.
CREATE OR REPLACE FUNCTION trace.erase_subject(p_subject text) RETURNS integer
LANGUAGE plpgsql SET search_path = trace, ops, pg_temp AS $$
DECLARE
  n integer;
BEGIN
  IF p_subject IS NULL OR p_subject !~ '^[A-Za-z0-9][A-Za-z0-9:._-]{0,63}$' THEN
    RAISE EXCEPTION 'erase_subject needs a subject id' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  WITH touched AS (
    SELECT DISTINCT run_id FROM trace.spans WHERE subject_ids @> ARRAY[p_subject]
  ), runs AS (
    SELECT run_id FROM touched
    UNION
    SELECT tr2.run_id FROM ops.thread_runs tr1
      JOIN ops.thread_runs tr2 ON tr2.thread_id = tr1.thread_id
     WHERE tr1.run_id IN (SELECT run_id FROM touched)
  )
  UPDATE trace.spans s
     SET input_text = NULL, output_text = NULL, subject_ids = array_remove(s.subject_ids, p_subject),
         attrs = s.attrs || '{"erased": true}'::jsonb
   WHERE s.run_id IN (SELECT run_id FROM runs);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;

REVOKE ALL ON FUNCTION trace.erase_subject(text) FROM PUBLIC;

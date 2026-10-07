-- 102 B3: minimum group size for trace.dept_stats (architecture §9 "open"; issues-log D-04).
-- A head sees span counts of their own department. In a small department (legal has 4 people) a
-- row with a handful of spans points at one person's activity, so rows below 5 spans, or with
-- spans from fewer than 5 distinct people, are not returned. Same access check as migration 002.

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
    HAVING count(*) >= 5 AND count(DISTINCT s.user_id) >= 5
    ORDER BY 1, 2, 3, 4, 5;
END
$$;

REVOKE ALL ON FUNCTION trace.dept_stats(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trace.dept_stats(timestamptz) TO corpai_app;

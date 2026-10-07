-- 062 B2: department headcount for hris.headcount (architecture §6 hr.aggregate, decisions D7).
--
-- hr.employees rows are readable only by HR and an employee's direct manager, so the CEO and the
-- other heads cannot count a department through RLS. This function returns people counts only
-- (no names, no pay) and repeats the hr.aggregate rules: the CEO sees every department, any other
-- head only their own. The Python tool also runs policy.decide() and hides groups under 5 people
-- (obligation min_group_5). HR staff do not need it: they read every employee row through RLS.

CREATE OR REPLACE FUNCTION ops.hr_headcount()
RETURNS TABLE (dept text, n bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = hr, pg_temp AS $$
DECLARE
  v_ceo boolean := core.cur_role() = 'ceo' AND 'role:ceo' = ANY (core.cur_groups());
  v_head boolean := core.cur_role() = 'manager' AND ('head:' || core.cur_dept()) = ANY (core.cur_groups());
BEGIN
  IF core.cur_user() IS NULL OR core.cur_clearance() <= 0 OR NOT (v_ceo OR v_head) THEN
    RAISE EXCEPTION 'only the CEO and department heads may read headcount aggregates' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
    SELECT e.dept, count(*)::bigint FROM hr.employees e
    WHERE v_ceo OR e.dept = core.cur_dept()
    GROUP BY e.dept ORDER BY e.dept;
END
$$;

REVOKE ALL ON FUNCTION ops.hr_headcount() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.hr_headcount() TO corpai_app;

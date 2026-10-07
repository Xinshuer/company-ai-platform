-- 091 M4 HR headcount aggregate (decision D7; design-review 五 薪酬、员工档案; architecture §6 hr.aggregate).
-- The one department-headcount implementation (issues-log D-10): hris.headcount and the HR headcount
-- summary both call it.
-- Staff counts per department come from hr.employees, which RLS shows only to HR and to direct
-- managers, so a head or the CEO cannot count them through RLS. This SECURITY DEFINER function returns
-- aggregates only, to exactly the callers the hr.aggregate policy rules allow:
--   the CEO and the head of HR: every department; any other head: their own department only.
-- HR staff and everybody else get no rows (the policy has no rule for them either).
-- Groups under 5 people are suppressed here, in SQL, so no caller can obtain a small count.
-- People are counted as at the start of `p_as_of` (default: today), the same rule as the headcount
-- metric: hired before it and not left before it (left_on is the last working day; issues-log D-12).
-- Callers pass the simulation date (corpai.clock.today()).

CREATE OR REPLACE FUNCTION hr.headcount_by_dept(p_as_of date DEFAULT NULL)
RETURNS TABLE (dept text, employees int, suppressed boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = hr, core, pg_temp AS $$
  WITH caller AS (
    SELECT core.cur_user() AS uid, core.cur_role() AS role, core.cur_dept() AS dept, core.cur_groups() AS grps,
           core.cur_clearance() AS clearance
  ), staff AS (
    SELECT e.dept FROM hr.employees e
     WHERE e.hired_on < coalesce(p_as_of, current_date)
       AND (e.left_on IS NULL OR e.left_on >= coalesce(p_as_of, current_date))
  ), allowed AS (
    SELECT d.dept
      FROM (SELECT DISTINCT s.dept FROM staff s) d, caller c
     WHERE c.uid IS NOT NULL AND c.clearance > 0
       AND (   (c.role = 'ceo' AND 'role:ceo' = ANY (c.grps))
            OR (c.role = 'manager' AND c.dept = 'hr' AND 'head:hr' = ANY (c.grps))
            OR (c.role = 'manager' AND d.dept = c.dept AND ('head:' || c.dept) = ANY (c.grps)))
  )
  SELECT a.dept,
         CASE WHEN count(s.*) >= 5 THEN count(s.*)::int END AS employees,
         count(s.*) < 5 AS suppressed
    FROM allowed a JOIN staff s ON s.dept = a.dept
   GROUP BY a.dept
   ORDER BY a.dept
$$;

REVOKE ALL ON FUNCTION hr.headcount_by_dept(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hr.headcount_by_dept(date) TO corpai_app;

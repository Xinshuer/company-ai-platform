-- 091 M4 HR headcount aggregate (decision D7; design-review 五 薪酬、员工档案; architecture §6 hr.aggregate).
-- Staff counts per department come from hr.employees, which RLS shows only to HR and to direct
-- managers, so a head or the CEO cannot count them through RLS. This SECURITY DEFINER function returns
-- aggregates only, to exactly the callers the hr.aggregate policy rules allow:
--   the CEO and the head of HR: every department; any other head: their own department only.
-- Groups under 5 people are suppressed here, in SQL, so no caller can obtain a small count.

CREATE OR REPLACE FUNCTION hr.headcount_by_dept()
RETURNS TABLE (dept text, employees int, suppressed boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = hr, core, pg_temp AS $$
  WITH caller AS (
    SELECT core.cur_user() AS uid, core.cur_role() AS role, core.cur_dept() AS dept, core.cur_groups() AS grps,
           core.cur_clearance() AS clearance
  ), allowed AS (
    SELECT d.dept
      FROM (SELECT DISTINCT e.dept FROM hr.employees e) d, caller c
     WHERE c.uid IS NOT NULL AND c.clearance > 0
       AND (   (c.role = 'ceo' AND 'role:ceo' = ANY (c.grps))
            OR (c.role = 'manager' AND c.dept = 'hr' AND 'head:hr' = ANY (c.grps))
            OR (c.role = 'manager' AND d.dept = c.dept AND ('head:' || c.dept) = ANY (c.grps)))
  )
  SELECT a.dept,
         CASE WHEN count(e.*) >= 5 THEN count(e.*)::int END AS employees,
         count(e.*) < 5 AS suppressed
    FROM allowed a JOIN hr.employees e ON e.dept = a.dept
   GROUP BY a.dept
   ORDER BY a.dept
$$;

REVOKE ALL ON FUNCTION hr.headcount_by_dept() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hr.headcount_by_dept() TO corpai_app;

-- 062 B2: (retired) department headcount for hris.headcount.
--
-- B2 first added ops.hr_headcount() here, while M4 added hr.headcount_by_dept() (migration 091).
-- Two implementations of one aggregate disagreed (this one returned groups under 5 people to Python
-- and did not let the head of HR see every department), so issues-log D-10 kept 091 only:
-- hris.headcount, the HR headcount summary and every other caller use hr.headcount_by_dept().

DROP FUNCTION IF EXISTS ops.hr_headcount();

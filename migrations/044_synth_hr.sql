-- 044 A5 synth: HR. Employees L3 (HR + direct manager via mgr-of); compensation and performance L4 (head:hr only);
-- requisitions L2; requisition budgets L3; candidates L3 (req panel, head:hr); screenings L4. See architecture §5.1.
-- Pay and ratings live in their own tables so RLS alone protects them (no column masking needed).
-- In employees and requisitions the business column `dept` is also the label's dept.

CREATE TABLE IF NOT EXISTS hr.employees (
  employee_id     text PRIMARY KEY,
  name            text NOT NULL,
  dept            text NOT NULL CHECK (dept IN ('sales','eng','legal','finance','hr','exec','it')),
  title           text NOT NULL,
  manager_id      text,
  country         text,
  hired_on        date NOT NULL,
  -- last working day of a former employee; NULL while employed. HR keeps leavers' records, so
  -- headcount and attrition at any date come from hired_on / left_on (issues-log D-12).
  left_on         date CHECK (left_on IS NULL OR left_on >= hired_on),
  employment_type text NOT NULL CHECK (employment_type IN ('full_time','part_time','contractor')),
  fte             numeric(3,2) NOT NULL,
  level           smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  owner_id        text,
  acl_groups      text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust           text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids     text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hr.compensation (
  employee_id      text PRIMARY KEY REFERENCES hr.employees(employee_id),
  base_salary_eur  numeric(12,2) NOT NULL,
  bonus_target_pct numeric(5,2) NOT NULL,
  effective_on     date NOT NULL,
  level            smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept             text,
  owner_id         text,
  acl_groups       text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust            text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids      text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hr.performance (
  employee_id text NOT NULL REFERENCES hr.employees(employee_id),
  period      text NOT NULL,
  rating      smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
  summary     text NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (employee_id, period)
);

CREATE TABLE IF NOT EXISTS hr.requisitions (
  req_id            text PRIMARY KEY CHECK (req_id ~ '^R-[0-9]{2}$'),
  dept              text NOT NULL CHECK (dept IN ('sales','eng','legal','finance','hr','exec','it')),
  title             text NOT NULL,
  headcount         int NOT NULL CHECK (headcount > 0),
  status            text NOT NULL CHECK (status IN ('open','on_hold','filled','closed')),
  opened_on         date NOT NULL,
  -- the day the current status began (issues-log D-12): period metrics use the status at the
  -- period's end. Each date is set exactly when the requisition is in that status.
  on_hold_on        date CHECK (on_hold_on IS NULL OR on_hold_on >= opened_on),
  filled_on         date CHECK (filled_on IS NULL OR filled_on >= opened_on),
  closed_on         date CHECK (closed_on IS NULL OR closed_on >= opened_on),
  hiring_manager_id text NOT NULL,
  recruiter_id      text NOT NULL,
  jd_text           text NOT NULL,
  hard_requirements jsonb NOT NULL DEFAULT '[]',
  level             smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  owner_id          text,
  acl_groups        text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust             text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids       text[] NOT NULL DEFAULT '{}',
  CHECK ((status = 'on_hold') = (on_hold_on IS NOT NULL) AND (status = 'filled') = (filled_on IS NOT NULL)
         AND (status = 'closed') = (closed_on IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS hr.requisition_budgets (
  req_id              text PRIMARY KEY REFERENCES hr.requisitions(req_id),
  approved_budget_eur numeric(12,2) NOT NULL,
  projected_cost_eur  numeric(12,2) NOT NULL,
  comp_band_min       numeric(12,2) NOT NULL,
  comp_band_max       numeric(12,2) NOT NULL,
  level               smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept                text,
  owner_id            text,
  acl_groups          text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust               text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids         text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hr.candidates (
  candidate_id text PRIMARY KEY,
  req_id       text NOT NULL REFERENCES hr.requisitions(req_id),
  name         text NOT NULL,
  email        text NOT NULL CHECK (email LIKE '%.example'),
  phone        text,
  resume_text  text NOT NULL,                -- untrusted input: may carry injections (planted)
  source       text NOT NULL,
  applied_on   date NOT NULL,
  stage        text NOT NULL CHECK (stage IN ('applied','screening','interview','offer','rejected','hired')),
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'untrusted' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}'
);

-- Written later by the HR screening executor (owner role). Evidence per hard requirement; the decision
-- is a person's (GDPR Art. 22, AI Act Annex III 4): a decision needs a reason and who took it.
CREATE TABLE IF NOT EXISTS hr.screenings (
  screening_id text PRIMARY KEY,
  candidate_id text NOT NULL REFERENCES hr.candidates(candidate_id),
  req_id       text NOT NULL REFERENCES hr.requisitions(req_id),
  created_by   text NOT NULL,
  evidence     jsonb NOT NULL DEFAULT '[]',
  decision     text,
  reason       text,
  decided_by   text,
  decided_at   timestamptz,
  level        smallint NOT NULL DEFAULT 4 CHECK (level = 4),
  dept         text DEFAULT 'hr',
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}',
  CHECK (decision IS NULL OR (reason IS NOT NULL AND length(trim(reason)) > 0 AND decided_by IS NOT NULL
                              AND decided_at IS NOT NULL))
);

-- Fill the §5.1 label for screenings when a writer omits it, so a forgotten ACL cannot widen access.
CREATE OR REPLACE FUNCTION hr.screenings_default_label() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.acl_groups IS NULL THEN
    NEW.acl_groups := ARRAY['req:' || NEW.req_id, 'head:hr'];
  END IF;
  IF NEW.subject_ids IS NULL OR cardinality(NEW.subject_ids) = 0 THEN
    NEW.subject_ids := ARRAY[NEW.candidate_id];
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS screenings_default_label ON hr.screenings;
CREATE TRIGGER screenings_default_label BEFORE INSERT ON hr.screenings
  FOR EACH ROW EXECUTE FUNCTION hr.screenings_default_label();

CREATE INDEX IF NOT EXISTS candidates_req_idx ON hr.candidates (req_id);

ALTER TABLE hr.employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.compensation ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.requisitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.requisition_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.screenings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS employees_read ON hr.employees;
CREATE POLICY employees_read ON hr.employees FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS compensation_read ON hr.compensation;
CREATE POLICY compensation_read ON hr.compensation FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS performance_read ON hr.performance;
CREATE POLICY performance_read ON hr.performance FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS requisitions_read ON hr.requisitions;
CREATE POLICY requisitions_read ON hr.requisitions FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS requisition_budgets_read ON hr.requisition_budgets;
CREATE POLICY requisition_budgets_read ON hr.requisition_budgets FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS candidates_read ON hr.candidates;
CREATE POLICY candidates_read ON hr.candidates FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS screenings_read ON hr.screenings;
CREATE POLICY screenings_read ON hr.screenings FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

GRANT SELECT ON hr.employees, hr.compensation, hr.performance, hr.requisitions, hr.requisition_budgets,
  hr.candidates, hr.screenings TO corpai_app;

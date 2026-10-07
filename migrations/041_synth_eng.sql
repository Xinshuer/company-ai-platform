-- 041 A5 synth: engineering. Projects L3 (proj, eng, sales, CEO); tickets and CI runs L3 (proj, head:eng);
-- vulnerabilities L4 (head:eng, CEO). See architecture §5.1.

CREATE TABLE IF NOT EXISTS eng.projects (
  project_id          text PRIMARY KEY CHECK (project_id ~ '^P-[0-9]{2}$'),
  name                text NOT NULL,
  customer_account_id text REFERENCES crm.accounts(account_id),
  stage               text NOT NULL CHECK (stage IN ('requirements','approved','development','testing','release','operations')),
  stage_since         date NOT NULL,
  planned_go_live     date,
  health              text NOT NULL CHECK (health IN ('green','amber','red')),
  lead_id             text NOT NULL,
  repo                text NOT NULL,
  summary             text NOT NULL,
  level               smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept                text,
  owner_id            text,
  acl_groups          text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust               text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids         text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS eng.tickets (
  ticket_id   text PRIMARY KEY CHECK (ticket_id ~ '^FP-[0-9]+$'),
  project_id  text NOT NULL REFERENCES eng.projects(project_id),
  title       text NOT NULL,
  description text NOT NULL,
  type        text NOT NULL CHECK (type IN ('bug','story','task','chore')),
  status      text NOT NULL CHECK (status IN ('open','in_progress','in_review','done')),
  assignee_id text,
  created_on  date NOT NULL,
  updated_on  date NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS eng.ci_runs (
  run_id        text PRIMARY KEY,
  project_id    text NOT NULL REFERENCES eng.projects(project_id),
  branch        text NOT NULL,
  status        text NOT NULL CHECK (status IN ('passed','failed')),
  started_at    timestamptz NOT NULL,
  commit_sha    text NOT NULL,
  failing_tests text[] NOT NULL DEFAULT '{}',
  level         smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept          text,
  owner_id      text,
  acl_groups    text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust         text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids   text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS eng.vulnerabilities (
  vuln_id     text PRIMARY KEY,
  project_id  text NOT NULL REFERENCES eng.projects(project_id),
  severity    text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
  title       text NOT NULL,
  description text NOT NULL,
  status      text NOT NULL CHECK (status IN ('open','mitigating','fixed')),
  found_on    date NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS tickets_project_idx ON eng.tickets (project_id);
CREATE INDEX IF NOT EXISTS ci_runs_project_idx ON eng.ci_runs (project_id, started_at);

ALTER TABLE eng.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE eng.tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE eng.ci_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE eng.vulnerabilities ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS projects_read ON eng.projects;
CREATE POLICY projects_read ON eng.projects FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS tickets_read ON eng.tickets;
CREATE POLICY tickets_read ON eng.tickets FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS ci_runs_read ON eng.ci_runs;
CREATE POLICY ci_runs_read ON eng.ci_runs FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS vulnerabilities_read ON eng.vulnerabilities;
CREATE POLICY vulnerabilities_read ON eng.vulnerabilities FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

GRANT SELECT ON eng.projects, eng.tickets, eng.ci_runs, eng.vulnerabilities TO corpai_app;

-- 043 A5 synth: finance. Ledger, invoices, budgets L3 (dept:finance, head:finance, CEO); chart of accounts L2.
-- Financial statements and funding memos are corpus documents at L4. See architecture §5.1.
-- In journal_entries and budgets the business column `dept` (cost centre) is also the label's dept.

CREATE TABLE IF NOT EXISTS fin.chart_of_accounts (
  account_code text PRIMARY KEY,
  name         text NOT NULL,
  type         text NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense')),
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fin.journal_entries (
  entry_id            text PRIMARY KEY,          -- one row per journal line
  txn_id              text NOT NULL,             -- lines of one balanced transaction share it
  posted_on           date NOT NULL,
  period              text NOT NULL CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
  account_code        text NOT NULL REFERENCES fin.chart_of_accounts(account_code),
  dept                text NOT NULL CHECK (dept IN ('sales','eng','legal','finance','hr','exec','it')),
  description         text NOT NULL,
  debit_eur           numeric(14,2) NOT NULL DEFAULT 0 CHECK (debit_eur >= 0),
  credit_eur          numeric(14,2) NOT NULL DEFAULT 0 CHECK (credit_eur >= 0),
  project_id          text REFERENCES eng.projects(project_id),
  customer_account_id text REFERENCES crm.accounts(account_id),
  status              text NOT NULL CHECK (status IN ('draft','posted')),
  level               smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  owner_id            text,
  acl_groups          text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust               text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids         text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fin.invoices (
  invoice_id          text PRIMARY KEY,
  customer_account_id text NOT NULL REFERENCES crm.accounts(account_id),
  issued_on           date NOT NULL,
  due_on              date NOT NULL,
  amount_eur          numeric(14,2) NOT NULL CHECK (amount_eur > 0),
  status              text NOT NULL CHECK (status IN ('paid','open','overdue')),
  paid_on             date,
  kind                text NOT NULL CHECK (kind IN ('subscription','services')),
  level               smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept                text,
  owner_id            text,
  acl_groups          text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust               text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids         text[] NOT NULL DEFAULT '{}',
  CHECK ((status = 'paid') = (paid_on IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS fin.budgets (
  dept        text NOT NULL CHECK (dept IN ('sales','eng','legal','finance','hr','exec','it')),
  period      text NOT NULL CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
  budget_eur  numeric(14,2) NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (dept, period)
);

CREATE INDEX IF NOT EXISTS journal_period_idx ON fin.journal_entries (period, dept);
CREATE INDEX IF NOT EXISTS journal_txn_idx ON fin.journal_entries (txn_id);
CREATE INDEX IF NOT EXISTS invoices_customer_idx ON fin.invoices (customer_account_id, due_on);

ALTER TABLE fin.chart_of_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.budgets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chart_of_accounts_read ON fin.chart_of_accounts;
CREATE POLICY chart_of_accounts_read ON fin.chart_of_accounts FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS journal_entries_read ON fin.journal_entries;
CREATE POLICY journal_entries_read ON fin.journal_entries FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS invoices_read ON fin.invoices;
CREATE POLICY invoices_read ON fin.invoices FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS budgets_read ON fin.budgets;
CREATE POLICY budgets_read ON fin.budgets FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

GRANT SELECT ON fin.chart_of_accounts, fin.journal_entries, fin.invoices, fin.budgets TO corpai_app;

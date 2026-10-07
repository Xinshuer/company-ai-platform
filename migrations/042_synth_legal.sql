-- 042 A5 synth: legal. Contracts L3 (handling lawyer, head:legal, CEO); contract requests L3 (requesting rep,
-- head:sales, legal); matters L4 (matter group, head:legal; the CEO needs a grant); regulations L1 (all);
-- clause library L3 (legal dept). See architecture §5.1.

CREATE TABLE IF NOT EXISTS legal.contracts (
  contract_id             text PRIMARY KEY CHECK (contract_id ~ '^C-[0-9]{4}$'),
  account_id              text NOT NULL REFERENCES crm.accounts(account_id),
  counterparty            text NOT NULL,
  contract_type           text NOT NULL CHECK (contract_type IN ('MSA','DPA','NDA','SOW','order_form')),
  status                  text NOT NULL CHECK (status IN ('draft','negotiation','signed','expired','terminated')),
  signed_on               date,
  effective_on            date,
  expires_on              date,
  auto_renew              boolean NOT NULL DEFAULT false,
  notice_days             int NOT NULL DEFAULT 90,
  governing_law           text NOT NULL,
  has_dpa_clause          boolean NOT NULL DEFAULT false,
  processes_personal_data boolean NOT NULL DEFAULT false,  -- processor relationship: needs Art. 28 terms
  liability_cap_eur       numeric(14,2),
  handler_id              text NOT NULL,
  version                 int NOT NULL DEFAULT 1,
  text                    text NOT NULL,
  level                   smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept                    text,
  owner_id                text,
  acl_groups              text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust                   text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids             text[] NOT NULL DEFAULT '{}',
  CHECK (status NOT IN ('signed','expired','terminated') OR (signed_on IS NOT NULL AND expires_on IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS legal.contract_requests (
  request_id   text PRIMARY KEY,
  account_id   text NOT NULL REFERENCES crm.accounts(account_id),
  contract_id  text REFERENCES legal.contracts(contract_id),
  requested_by text NOT NULL,
  status       text NOT NULL CHECK (status IN ('requested','in_review','completed','rejected')),
  requested_on date NOT NULL,
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS legal.matters (
  matter_id    text PRIMARY KEY CHECK (matter_id ~ '^M-[0-9]{2}$'),
  title        text NOT NULL,
  kind         text NOT NULL,
  counterparty text NOT NULL,
  status       text NOT NULL CHECK (status IN ('open','settled','closed')),
  handler_id   text NOT NULL,
  opened_on    date NOT NULL,
  summary      text NOT NULL,
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS legal.regulations (
  reg_id      text PRIMARY KEY,
  title       text NOT NULL,
  status      text NOT NULL CHECK (status IN ('in_force','adopted_not_applicable','draft')),
  summary     text NOT NULL,
  source_uri  text NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS legal.clause_library (
  clause_id   text PRIMARY KEY,
  clause_type text NOT NULL,
  position    text NOT NULL CHECK (position IN ('preferred','fallback','unacceptable')),
  text        text NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS contracts_expires_idx ON legal.contracts (expires_on);
CREATE INDEX IF NOT EXISTS contracts_account_idx ON legal.contracts (account_id);

ALTER TABLE legal.contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal.contract_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal.matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal.regulations ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal.clause_library ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS contracts_read ON legal.contracts;
CREATE POLICY contracts_read ON legal.contracts FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS contract_requests_read ON legal.contract_requests;
CREATE POLICY contract_requests_read ON legal.contract_requests FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS matters_read ON legal.matters;
CREATE POLICY matters_read ON legal.matters FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS regulations_read ON legal.regulations;
CREATE POLICY regulations_read ON legal.regulations FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS clause_library_read ON legal.clause_library;
CREATE POLICY clause_library_read ON legal.clause_library FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

GRANT SELECT ON legal.contracts, legal.contract_requests, legal.matters, legal.regulations, legal.clause_library
  TO corpai_app;

-- 040 A5 synth: CRM. All rows L3; acl = {user:<owner rep>, head:sales, role:ceo, grant:crm:<account>} (architecture §5.1).
-- Writes happen only through owner-role jobs (synth, proposal executors), so corpai_app gets SELECT only.

CREATE TABLE IF NOT EXISTS crm.accounts (
  account_id  text PRIMARY KEY CHECK (account_id ~ '^A-[0-9]{4}$'),
  name        text NOT NULL,
  domain      text NOT NULL UNIQUE CHECK (domain LIKE '%.example'),
  industry    text NOT NULL,
  country     text NOT NULL CHECK (country IN ('DE','NL','FR','PL','AT','IT','ES')),
  city        text NOT NULL,
  size_band   text NOT NULL,
  employees   int NOT NULL,
  fleet_size  int NOT NULL,
  owner_id    text NOT NULL,
  tier        text NOT NULL CHECK (tier IN ('strategic','key','standard')),
  arr_eur     numeric(14,2) NOT NULL DEFAULT 0,
  status      text NOT NULL CHECK (status IN ('customer','prospect','churned')),
  created_on  date NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crm.contacts (
  contact_id  text PRIMARY KEY,
  account_id  text NOT NULL REFERENCES crm.accounts(account_id),
  name        text NOT NULL,
  title       text,
  email       text NOT NULL CHECK (email LIKE '%.example'),
  phone       text,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crm.opportunities (
  opp_id      text PRIMARY KEY,
  account_id  text NOT NULL REFERENCES crm.accounts(account_id),
  name        text NOT NULL,
  stage       text NOT NULL CHECK (stage IN ('prospecting','qualification','proposal','negotiation','closed_won','closed_lost')),
  amount_eur  numeric(14,2) NOT NULL,
  close_date  date NOT NULL,
  probability int NOT NULL CHECK (probability BETWEEN 0 AND 100),
  owner_id    text NOT NULL,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crm.interactions (
  interaction_id text PRIMARY KEY,
  account_id     text NOT NULL REFERENCES crm.accounts(account_id),
  contact_id     text REFERENCES crm.contacts(contact_id),
  kind           text NOT NULL CHECK (kind IN ('email','call','meeting','demo')),
  occurred_on    date NOT NULL,
  summary        text NOT NULL,
  sentiment      text NOT NULL CHECK (sentiment IN ('positive','neutral','negative')),
  owner_id       text NOT NULL,
  level          smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept           text,
  acl_groups     text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust          text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids    text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crm.quotes (
  quote_id       text PRIMARY KEY,
  account_id     text NOT NULL REFERENCES crm.accounts(account_id),
  opp_id         text REFERENCES crm.opportunities(opp_id),
  list_price_eur numeric(14,2) NOT NULL,
  discount_pct   numeric(5,2) NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
  net_eur        numeric(14,2) NOT NULL,
  valid_until    date NOT NULL,
  approved_by    text,
  level          smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept           text,
  owner_id       text,
  acl_groups     text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust          text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids    text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS contacts_account_idx ON crm.contacts (account_id);
CREATE INDEX IF NOT EXISTS opportunities_account_idx ON crm.opportunities (account_id);
CREATE INDEX IF NOT EXISTS interactions_account_idx ON crm.interactions (account_id, occurred_on);

-- RLS: no FORCE (owner jobs bypass); the app role reads through core.can_read only.
ALTER TABLE crm.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunities ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.quotes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS accounts_read ON crm.accounts;
CREATE POLICY accounts_read ON crm.accounts FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS contacts_read ON crm.contacts;
CREATE POLICY contacts_read ON crm.contacts FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS opportunities_read ON crm.opportunities;
CREATE POLICY opportunities_read ON crm.opportunities FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS interactions_read ON crm.interactions;
CREATE POLICY interactions_read ON crm.interactions FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS quotes_read ON crm.quotes;
CREATE POLICY quotes_read ON crm.quotes FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

GRANT SELECT ON crm.accounts, crm.contacts, crm.opportunities, crm.interactions, crm.quotes TO corpai_app;

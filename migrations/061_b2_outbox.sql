-- 061 B2: simulated outbound systems written by proposal executors (decisions D8).
--
-- ops.outbox: emails and contracts that were "sent" after approval. Nothing leaves the machine;
-- the egress inventory lists it as email.outbox (simulated). eng.ticket_comments: comments added
-- by code.comment_ticket (the A5 ticket table has no comment column).
-- Both are written by the owner role only (executors); the app role reads them through RLS.

CREATE TABLE IF NOT EXISTS ops.outbox (
  id          text PRIMARY KEY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  proposal_id text NOT NULL REFERENCES ops.proposals(id),
  channel     text NOT NULL CHECK (channel IN ('email', 'contract')),
  recipient   text NOT NULL CHECK (recipient ~ '^[^@\s]+@[^@\s]+\.example$'),  -- simulated world only
  subject     text NOT NULL,
  body        text NOT NULL,
  attachments jsonb NOT NULL DEFAULT '[]',
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed', 'internal', 'untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS outbox_proposal_idx ON ops.outbox (proposal_id);

ALTER TABLE ops.outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops.outbox FROM PUBLIC;
DROP POLICY IF EXISTS outbox_read ON ops.outbox;
CREATE POLICY outbox_read ON ops.outbox FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
GRANT SELECT ON ops.outbox TO corpai_app;

CREATE TABLE IF NOT EXISTS eng.ticket_comments (
  comment_id  text PRIMARY KEY,
  ticket_id   text NOT NULL REFERENCES eng.tickets(ticket_id) ON DELETE CASCADE,
  author_id   text NOT NULL,
  body        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  proposal_id text,
  level       smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept        text,
  owner_id    text,
  acl_groups  text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust       text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed', 'internal', 'untrusted')),
  subject_ids text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ticket_comments_ticket_idx ON eng.ticket_comments (ticket_id, created_at);

ALTER TABLE eng.ticket_comments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ticket_comments_read ON eng.ticket_comments;
CREATE POLICY ticket_comments_read ON eng.ticket_comments FOR SELECT TO corpai_app
  USING (core.can_read(level, acl_groups));
GRANT SELECT ON eng.ticket_comments TO corpai_app;

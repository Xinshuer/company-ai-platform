-- 045 A5 synth: evaluation answers. Owner only: no USAGE on the schema and no table grants for corpai_app,
-- so nothing on the request path (and no model behind it) can read the expected answers or the canary list.

CREATE SCHEMA IF NOT EXISTS synth;
REVOKE ALL ON SCHEMA synth FROM PUBLIC;

CREATE TABLE IF NOT EXISTS synth.planted (
  kind     text NOT NULL,
  ref      text NOT NULL,
  expected jsonb NOT NULL,
  note     text NOT NULL DEFAULT '',
  PRIMARY KEY (kind, ref)
);

CREATE TABLE IF NOT EXISTS synth.canaries (
  canary   text NOT NULL CHECK (canary ~ '^KB-CANARY-[0-9A-F]{8}$'),
  location text NOT NULL,                       -- db:<schema.table>:<pk> or file:<path under corpus_dir>
  level    smallint NOT NULL CHECK (level BETWEEN 1 AND 4),  -- level of the host row or document
  PRIMARY KEY (canary, location)
);

-- Defence in depth: even if someone grants access by mistake, no policy means no rows for corpai_app.
ALTER TABLE synth.planted ENABLE ROW LEVEL SECURITY;
ALTER TABLE synth.canaries ENABLE ROW LEVEL SECURITY;

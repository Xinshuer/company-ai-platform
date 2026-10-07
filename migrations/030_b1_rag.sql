-- 030 B1 rag: documents (one row per version) and their chunks with embeddings (architecture §10).
-- Chunks copy the document's label, collection and status so retrieval filters and RLS work on one
-- table. Writes only by the owner (ingestion job); corpai_app reads through RLS.

CREATE TABLE IF NOT EXISTS rag.documents (
  doc_id       text NOT NULL CHECK (doc_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  version      int  NOT NULL CHECK (version >= 1),
  status       text NOT NULL CHECK (status IN ('current','superseded','draft')),
  collection   text NOT NULL CHECK (collection IN ('public','general','eng','sales','legal','legal_regs',
                                                   'finance','hr','exec','playbooks')),
  title        text NOT NULL,
  source       text NOT NULL,
  source_uri   text NOT NULL DEFAULT '',
  project_id   text,
  matter_id    text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  content_hash text NOT NULL,                  -- sha256 of the text as ingested: same hash = same content
  pipeline     text NOT NULL,                  -- embedder + chunker version; a change re-embeds in place
  text         text NOT NULL,                  -- full text after secret scrubbing (docs.get_doc needs it)
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (doc_id, version)
);

-- At most one current version per document: retrieval of "status = current" can never mix versions.
CREATE UNIQUE INDEX IF NOT EXISTS documents_one_current ON rag.documents (doc_id) WHERE status = 'current';
CREATE INDEX IF NOT EXISTS documents_acl_gin ON rag.documents USING gin (acl_groups);

CREATE TABLE IF NOT EXISTS rag.chunks (
  chunk_id     text PRIMARY KEY,               -- "<doc_id>@v<version>#<ord>"
  doc_id       text NOT NULL,
  version      int  NOT NULL,
  ord          int  NOT NULL CHECK (ord >= 0),
  title        text NOT NULL,
  heading      text NOT NULL DEFAULT '',
  text         text NOT NULL,
  source_uri   text NOT NULL DEFAULT '',
  collection   text NOT NULL,
  status       text NOT NULL CHECK (status IN ('current','superseded','draft')),
  project_id   text,
  matter_id    text,
  embedding    vector(384) NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (
                 to_tsvector('english'::regconfig, title || ' ' || heading || ' ' || text)) STORED,
  level        smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  dept         text,
  owner_id     text,
  acl_groups   text[] NOT NULL CHECK (cardinality(acl_groups) > 0),
  trust        text NOT NULL DEFAULT 'internal' CHECK (trust IN ('reviewed','internal','untrusted')),
  subject_ids  text[] NOT NULL DEFAULT '{}',
  UNIQUE (doc_id, version, ord),
  FOREIGN KEY (doc_id, version) REFERENCES rag.documents (doc_id, version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON rag.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_acl_gin ON rag.chunks USING gin (acl_groups);
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON rag.chunks (doc_id, version);

-- RLS: ENABLE, not FORCE (the ingestion job is the owner and must write; see issues-log C-02).
ALTER TABLE rag.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS documents_read ON rag.documents;
CREATE POLICY documents_read ON rag.documents FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));
DROP POLICY IF EXISTS chunks_read ON rag.chunks;
CREATE POLICY chunks_read ON rag.chunks FOR SELECT TO corpai_app USING (core.can_read(level, acl_groups));

-- Read only for the app role; no INSERT/UPDATE/DELETE grants, so request paths cannot write RAG content.
REVOKE ALL ON rag.documents, rag.chunks FROM PUBLIC;
GRANT SELECT ON rag.documents, rag.chunks TO corpai_app;

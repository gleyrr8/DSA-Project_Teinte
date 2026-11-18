PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS source_file (
  id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
  parser_version TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0, UNIQUE(filename, sha256)
);
CREATE TABLE IF NOT EXISTS staged_record (
  id TEXT PRIMARY KEY, source_file_id TEXT NOT NULL REFERENCES source_file(id), record_type TEXT NOT NULL,
  source_row INTEGER NOT NULL, raw_payload TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT 'unreviewed',
  disposition_reason TEXT, created_at TEXT NOT NULL, UNIQUE(source_file_id, record_type, source_row)
);
CREATE TABLE IF NOT EXISTS brand (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft',
  publication_state TEXT NOT NULL DEFAULT 'Draft', evidence_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brand(id), name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]', version TEXT,
  format TEXT, lifecycle TEXT NOT NULL DEFAULT 'Unverified', publication_state TEXT NOT NULL DEFAULT 'Draft',
  evidence_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shade (
  id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(id), name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]', code TEXT,
  original_description TEXT, depth TEXT, undertone TEXT, normalization_state TEXT NOT NULL DEFAULT 'Incomplete',
  depth_confidence TEXT NOT NULL DEFAULT 'Unrated', undertone_confidence TEXT NOT NULL DEFAULT 'Unrated',
  availability TEXT NOT NULL DEFAULT 'Not Verified', availability_source TEXT, availability_checked_at TEXT,
  publication_state TEXT NOT NULL DEFAULT 'Draft', lifecycle TEXT NOT NULL DEFAULT 'Unverified',
  evidence_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_claim (
  id TEXT PRIMARY KEY, source_shade_id TEXT NOT NULL REFERENCES shade(id), target_shade_id TEXT REFERENCES shade(id),
  source_wording TEXT NOT NULL, closeness TEXT, confidence TEXT NOT NULL DEFAULT 'Unrated', caveat TEXT,
  source_file TEXT, source_page TEXT, evidence_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'Draft',
  checked_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correction_report (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, description TEXT NOT NULL, contact TEXT,
  status TEXT NOT NULL DEFAULT 'Under Review', created_at TEXT NOT NULL, retain_until TEXT NOT NULL,
  admin_notes TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_event (
  id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL,
  previous_json TEXT, proposed_json TEXT, reason TEXT NOT NULL, actor TEXT NOT NULL,
  review_mode TEXT NOT NULL DEFAULT 'self-review', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_session (
  token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shade_product ON shade(product_id);
CREATE INDEX IF NOT EXISTS idx_claim_source ON match_claim(source_shade_id, status);
CREATE INDEX IF NOT EXISTS idx_stage_type ON staged_record(record_type, disposition);
CREATE INDEX IF NOT EXISTS idx_correction_queue ON correction_report(status, created_at);

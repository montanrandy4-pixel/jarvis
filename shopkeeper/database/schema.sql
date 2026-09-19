-- Everything the agent has seen, decided and said.
--
-- SQLite because this is one shop on one machine: a single file, no daemon,
-- no connection pool, and a backup is `cp`. WAL mode so the dashboard can read
-- while the agent writes.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per agent pass. The audit trail for "what was it doing at 3am".
CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at     TEXT    NOT NULL,
  finished_at    TEXT,
  status         TEXT    NOT NULL DEFAULT 'running',  -- running|ok|failed|skipped
  trigger        TEXT    NOT NULL DEFAULT 'schedule', -- schedule|manual|startup
  -- Why a pass did nothing: unchanged facts means we skipped the model call.
  skip_reason    TEXT,
  facts_hash     TEXT,
  alerts_created INTEGER NOT NULL DEFAULT 0,
  input_tokens   INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0,
  cached_tokens  INTEGER NOT NULL DEFAULT 0,
  cost_usd       REAL    NOT NULL DEFAULT 0,
  duration_ms    INTEGER,
  error          TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  created_at   TEXT    NOT NULL,
  severity     TEXT    NOT NULL,          -- critical|warning|info
  category     TEXT    NOT NULL,          -- inventory|orders|refunds|sales|catalog|system
  title        TEXT    NOT NULL,
  detail       TEXT    NOT NULL DEFAULT '',
  recommendation TEXT  NOT NULL DEFAULT '',
  -- Stable identity, so the same condition does not alert every pass.
  fingerprint  TEXT    NOT NULL,
  subject      TEXT,                      -- product/order this is about
  acknowledged INTEGER NOT NULL DEFAULT 0,
  resolved_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_open
  ON alerts(fingerprint) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity, resolved_at);

-- Inventory over time, so "low stock" can mean "falling", not just "small".
CREATE TABLE IF NOT EXISTS inventory_snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  captured_at  TEXT    NOT NULL,
  product_id   TEXT    NOT NULL,
  variant_id   TEXT    NOT NULL,
  title        TEXT    NOT NULL,
  sku          TEXT,
  available    INTEGER,
  price        REAL
);
CREATE INDEX IF NOT EXISTS idx_inv_variant ON inventory_snapshots(variant_id, captured_at DESC);

-- Anything the agent did or was asked. Includes manual prompts.
CREATE TABLE IF NOT EXISTS activity (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT    NOT NULL,
  kind        TEXT    NOT NULL,   -- pass|prompt|error|startup|shopify
  summary     TEXT    NOT NULL,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at DESC);

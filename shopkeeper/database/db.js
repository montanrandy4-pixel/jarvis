/**
 * The database, and the few queries the app actually runs.
 *
 * better-sqlite3 is synchronous by design. For a single-process agent doing a
 * handful of writes per pass that is a feature, not a limitation: no callback
 * plumbing, no pool, and every write is durable before the next line runs.
 */
import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export function openDatabase(file) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new Database(file);
  db.pragma('journal_mode = WAL');
  db.exec(fs.readFileSync(path.join(here, 'schema.sql'), 'utf8'));
  return db;
}

export function createStore(db) {
  const now = () => new Date().toISOString();

  return {
    db,

    // ---- runs -----------------------------------------------------------
    startRun(trigger = 'schedule') {
      const { lastInsertRowid } = db
        .prepare('INSERT INTO runs (started_at, trigger) VALUES (?, ?)')
        .run(now(), trigger);
      return lastInsertRowid;
    },

    finishRun(id, patch) {
      const fields = ['status', 'skip_reason', 'facts_hash', 'alerts_created',
        'input_tokens', 'output_tokens', 'cached_tokens', 'cost_usd',
        'duration_ms', 'error'];
      const set = fields.filter((f) => patch[f] !== undefined);
      if (!set.length) return;
      db.prepare(
        `UPDATE runs SET finished_at = ?, ${set.map((f) => `${f} = ?`).join(', ')} WHERE id = ?`
      ).run(now(), ...set.map((f) => patch[f]), id);
    },

    lastRun() {
      return db.prepare('SELECT * FROM runs ORDER BY id DESC LIMIT 1').get() ?? null;
    },

    recentRuns(limit = 20) {
      return db.prepare('SELECT * FROM runs ORDER BY id DESC LIMIT ?').all(limit);
    },

    // ---- alerts ---------------------------------------------------------
    /**
     * Insert unless the same condition is already open.
     * The partial unique index on (fingerprint) WHERE resolved_at IS NULL does
     * the deduplication in the database rather than in a race-prone read/write.
     */
    addAlert(alert) {
      const result = db.prepare(`
        INSERT INTO alerts (run_id, created_at, severity, category, title,
                            detail, recommendation, fingerprint, subject)
        VALUES (@run_id, @created_at, @severity, @category, @title,
                @detail, @recommendation, @fingerprint, @subject)
        ON CONFLICT DO NOTHING
      `).run({
        run_id: alert.run_id ?? null,
        created_at: now(),
        severity: alert.severity,
        category: alert.category,
        title: alert.title,
        detail: alert.detail ?? '',
        recommendation: alert.recommendation ?? '',
        fingerprint: alert.fingerprint,
        subject: alert.subject ?? null,
      });
      return result.changes > 0;
    },

    /** Close anything no longer reported, so the feed reflects reality. */
    resolveMissing(openFingerprints) {
      const open = db.prepare(
        'SELECT id, fingerprint FROM alerts WHERE resolved_at IS NULL'
      ).all();
      const keep = new Set(openFingerprints);
      const stale = open.filter((row) => !keep.has(row.fingerprint));
      if (!stale.length) return [];
      const close = db.prepare('UPDATE alerts SET resolved_at = ? WHERE id = ?');
      const tx = db.transaction((rows) => rows.forEach((r) => close.run(now(), r.id)));
      tx(stale);
      return stale.map((r) => r.fingerprint);
    },

    listAlerts({ limit = 100, includeResolved = false, severity } = {}) {
      const where = [];
      const args = [];
      if (!includeResolved) where.push('resolved_at IS NULL');
      if (severity) { where.push('severity = ?'); args.push(severity); }
      const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';
      return db.prepare(
        `SELECT * FROM alerts ${clause} ORDER BY
           CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
           created_at DESC
         LIMIT ?`
      ).all(...args, limit);
    },

    acknowledgeAlert(id) {
      return db.prepare('UPDATE alerts SET acknowledged = 1 WHERE id = ?')
        .run(id).changes > 0;
    },

    alertCounts() {
      const rows = db.prepare(`
        SELECT severity, COUNT(*) AS n FROM alerts
        WHERE resolved_at IS NULL GROUP BY severity
      `).all();
      const out = { critical: 0, warning: 0, info: 0 };
      for (const row of rows) out[row.severity] = row.n;
      return out;
    },

    // ---- inventory ------------------------------------------------------
    recordInventory(rows) {
      if (!rows.length) return;
      const insert = db.prepare(`
        INSERT INTO inventory_snapshots
          (captured_at, product_id, variant_id, title, sku, available, price)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `);
      const stamp = now();
      db.transaction((items) => {
        for (const r of items) {
          insert.run(stamp, r.productId, r.variantId, r.title, r.sku,
                     r.available, r.price);
        }
      })(rows);
    },

    /** The previous level for each variant, for spotting a drop. */
    previousInventory() {
      const rows = db.prepare(`
        SELECT variant_id, available FROM inventory_snapshots
        WHERE captured_at = (
          SELECT captured_at FROM inventory_snapshots
          WHERE captured_at < (SELECT MAX(captured_at) FROM inventory_snapshots)
          ORDER BY captured_at DESC LIMIT 1
        )
      `).all();
      return Object.fromEntries(rows.map((r) => [r.variant_id, r.available]));
    },

    // ---- activity -------------------------------------------------------
    log(kind, summary, detail = null) {
      db.prepare(
        'INSERT INTO activity (created_at, kind, summary, detail) VALUES (?, ?, ?, ?)'
      ).run(now(), kind, summary, detail);
    },

    recentActivity(limit = 50) {
      return db.prepare('SELECT * FROM activity ORDER BY id DESC LIMIT ?').all(limit);
    },

    spendSince(iso) {
      const row = db.prepare(`
        SELECT COALESCE(SUM(cost_usd), 0) AS cost,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
               COUNT(*) AS runs
        FROM runs WHERE started_at >= ?
      `).get(iso);
      return row;
    },
  };
}

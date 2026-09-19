/**
 * One pass of the agent, and the schedule around it.
 *
 * Read the shop, compute the facts, run the rules over them. Store what comes
 * back, close what no longer applies.
 *
 * The loop is deliberately unkillable. A pass that throws is recorded as a
 * failed run and the next one goes ahead; an agent that dies quietly at 3am is
 * worse than no agent, because you believe you are being watched.
 */
import { EventEmitter } from 'node:events';
import { gather } from './lib/conditions.js';
import { RulesAgent, describeError, fingerprint } from './lib/rules.js';

export class Agent extends EventEmitter {
  constructor({ config, store, shopify, rules }) {
    super();
    this.config = config;
    this.store = store;
    this.shopify = shopify;
    this.rules = rules ?? new RulesAgent({ config });
    this.timer = null;
    this.running = false;
    this.lastFacts = null;
    this.lastHash = null;
    this.startedAt = null;
  }

  status() {
    const last = this.store.lastRun();
    return {
      active: this.timer !== null,
      running: this.running,
      startedAt: this.startedAt,
      intervalMinutes: this.config.intervalMinutes,
      nextRunAt: this.timer && last?.finished_at
        ? new Date(new Date(last.finished_at).getTime()
            + this.config.intervalMinutes * 60_000).toISOString()
        : null,
      lastRun: last,
      alerts: this.store.alertCounts(),
    };
  }

  /** The facts from the most recent pass, for answering questions. */
  async currentFacts() {
    if (this.lastFacts) return this.lastFacts;
    const { facts } = await this.#collect();
    return facts;
  }

  async #collect() {
    const since = new Date(
      Date.now() - this.config.orderLookbackDays * 86_400_000
    ).toISOString().slice(0, 10);

    const [shop, products, orders] = await Promise.all([
      this.shopify.shopInfo(),
      this.shopify.products(),
      this.shopify.orders(since),
    ]);

    const result = gather({
      shop, products, orders,
      previousInventory: this.store.previousInventory(),
      config: this.config,
    });
    this.store.recordInventory(result.variants);
    this.lastFacts = result.facts;
    return result;
  }

  async runOnce(trigger = 'schedule') {
    if (this.running) return { skipped: 'a pass is already running' };
    this.running = true;
    const startedAt = Date.now();
    const runId = this.store.startRun(trigger);
    this.emit('run:start', { runId, trigger });

    try {
      const { facts, hash } = await this.#collect();

      // The rules are free to run, so they run every pass. The hash is still
      // recorded -- it is how the dashboard can say nothing has moved.
      const unchanged = hash === this.lastHash;
      const assessment = await this.rules.assess(facts);
      let created = 0;
      const open = [];

      for (const alert of assessment.alerts) {
        const fp = fingerprint(alert);
        open.push(fp);
        if (this.store.addAlert({ ...alert, run_id: runId, fingerprint: fp })) {
          created += 1;
          this.emit('alert', { ...alert, fingerprint: fp });
        }
      }

      const resolved = this.store.resolveMissing(open);
      for (const fp of resolved) this.emit('resolved', { fingerprint: fp });

      this.lastHash = hash;
      this.store.finishRun(runId, {
        status: 'ok',
        facts_hash: hash,
        alerts_created: created,
        unchanged: unchanged ? 1 : 0,
        duration_ms: Date.now() - startedAt,
      });
      this.store.log(
        'pass',
        assessment.summary || `${created} new alert(s)`,
        JSON.stringify({ created, resolved: resolved.length, unchanged })
      );
      this.emit('run:end', { runId, created, resolved: resolved.length, unchanged });
      return { assessment, created, resolved: resolved.length, unchanged, facts };
    } catch (error) {
      const message = describeError(error);
      this.store.finishRun(runId, {
        status: 'failed',
        error: message,
        duration_ms: Date.now() - startedAt,
      });
      this.store.log('error', `Pass failed: ${message}`);
      this.emit('run:error', { runId, error: message });
      return { error: message };
    } finally {
      this.running = false;
    }
  }

  start() {
    if (this.timer) return;
    this.startedAt = new Date().toISOString();
    this.store.log('startup', `Agent started, checking every ${this.config.intervalMinutes}m`);
    const tick = () => {
      this.runOnce('schedule').catch((error) => {
        // runOnce handles its own errors; this is the last line of defence.
        this.store.log('error', `Unhandled: ${error?.message ?? error}`);
      });
    };
    this.timer = setInterval(tick, this.config.intervalMinutes * 60_000);
    if (this.config.runOnStart) setTimeout(tick, 1500);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

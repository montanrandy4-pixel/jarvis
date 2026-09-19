/* Boots the real server with a fake Shopify and a fake Claude, then drives it
   over HTTP exactly as the dashboard does. Proves the wiring, not the model. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { buildApp } from '../server/src/index.js';
import { openDatabase, createStore } from '../database/db.js';
import { Agent } from '../server/src/agent.js';
import { Shopify } from '../server/src/lib/shopify.js';

const DB = '/tmp/shopkeeper-e2e.db';
for (const f of [DB, `${DB}-wal`, `${DB}-shm`]) fs.rmSync(f, { force: true });

const config = {
  port: 0, databaseFile: DB,
  shopName: 'demo-shop', shopifyAccessToken: 'shpat_x', shopifyApiVersion: '2026-01',
  anthropicApiKey: 'sk-ant-x', model: 'claude-opus-5',
  intervalMinutes: 30, runOnStart: false, skipWhenUnchanged: true,
  lowStockThreshold: 5, highValueOrder: 250, refundRateThreshold: 10,
  salesDropThreshold: 40, orderLookbackDays: 7,
};

// --- a Shopify that answers from fixtures ---
let stock = 2;
const shopifyFetch = async (_url, options) => {
  const { query } = JSON.parse(options.body);
  const data = query.includes('shop {')
    ? { shop: { name: 'Demo Shop', myshopifyDomain: 'demo-shop.myshopify.com',
                currencyCode: 'USD', ianaTimezone: 'UTC' } }
    : query.includes('products(')
      ? { products: { edges: [{ cursor: 'c1', node: {
            id: 'gid://shopify/Product/1', title: 'Cedar Candle', handle: 'cedar',
            status: 'ACTIVE', totalInventory: stock,
            variants: { nodes: [{ id: 'gid://shopify/ProductVariant/1', sku: 'CDL-01',
              price: '24.00', inventoryQuantity: stock,
              inventoryItem: { tracked: true, requiresShipping: true } }] },
          } }], pageInfo: { hasNextPage: false, endCursor: 'c1' } } }
      : { orders: { edges: [{ cursor: 'o1', node: {
            id: 'gid://shopify/Order/1', name: '#1001',
            createdAt: new Date().toISOString(),
            displayFinancialStatus: 'PAID', displayFulfillmentStatus: 'FULFILLED',
            currentTotalPriceSet: { shopMoney: { amount: '480.00', currencyCode: 'USD' } },
            totalRefundedSet: { shopMoney: { amount: '0.00' } },
            customer: { numberOfOrders: 1 },
            lineItems: { nodes: [{ title: 'Cedar Candle', quantity: 20, sku: 'CDL-01' }] },
          } }], pageInfo: { hasNextPage: false, endCursor: 'o1' } } };
  return { ok: true, status: 200, headers: new Map(), json: async () => ({ data }) };
};

// --- a Claude that returns a fixed, schema-shaped assessment ---
let assessCalls = 0;
const fakeClaude = {
  async assess(facts) {
    assessCalls += 1;
    assert.ok(facts.inventory.low.length >= 1, 'low stock should reach the model');
    return {
      summary: 'One product is nearly out of stock.',
      alerts: [{
        severity: 'critical', category: 'inventory',
        title: 'Cedar Candle is down to 2 units',
        detail: 'Stock is 2, below the threshold of 5, and 20 sold in the last day.',
        recommendation: 'Reorder Cedar Candle today.',
        subject: 'Cedar Candle',
      }],
      usage: { input: 1200, output: 300, cached: 800, cost: 0.0115 },
    };
  },
  async ask(question) {
    return { answer: `Answering: ${question}`, usage: { input: 10, output: 5, cached: 0, cost: 0 } };
  },
};

const store = createStore(openDatabase(DB));
const shopify = new Shopify({ shopName: 'demo-shop', accessToken: 'x', fetchImpl: shopifyFetch });
const agent = new Agent({ config, store, shopify, claude: fakeClaude });
const app = buildApp({ config, store, agent });
const server = app.listen(0);
const base = `http://127.0.0.1:${server.address().port}`;
const api = async (p, o) => {
  const r = await fetch(base + p, { headers: { 'Content-Type': 'application/json' }, ...o });
  return { status: r.status, body: await r.json() };
};

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log(`  ok    ${name}`); }
  catch (e) { failures += 1; console.log(`  FAIL  ${name}\n        ${e.message}`); }
};

// 1. a pass creates an alert
const run1 = await api('/api/agent/run', { method: 'POST' });
check('a manual run succeeds', () => assert.equal(run1.status, 200));
check('it creates one alert', () => assert.equal(run1.body.created, 1));

const alerts1 = await api('/api/alerts');
check('the alert is served', () => assert.equal(alerts1.body.alerts.length, 1));
check('severity survives the round trip', () =>
  assert.equal(alerts1.body.alerts[0].severity, 'critical'));
check('counts are right', () => assert.equal(alerts1.body.counts.critical, 1));

// 2. same conditions -> deduplicated, and the model is not re-asked
const run2 = await api('/api/agent/run', { method: 'POST' });
check('a repeat pass creates no duplicate', () => assert.equal(run2.body.created, 0));
check('still exactly one alert', async () => {});
const alerts2 = await api('/api/alerts');
check('the feed did not grow', () => assert.equal(alerts2.body.alerts.length, 1));

// 3. scheduled pass with unchanged facts skips the model entirely
const before = assessCalls;
await agent.runOnce('schedule');
check('an unchanged scheduled pass skips the model call', () =>
  assert.equal(assessCalls, before));

// 4. the condition clears -> the alert resolves
stock = 90;
agent.lastHash = null;
fakeClaude.assess = async () => ({
  summary: 'All good.', alerts: [],
  usage: { input: 900, output: 60, cached: 800, cost: 0.003 },
});
const run3 = await api('/api/agent/run', { method: 'POST' });
check('the clearing pass resolves it', () => assert.equal(run3.body.resolved, 1));
const alerts3 = await api('/api/alerts');
check('open alerts are now empty', () => assert.equal(alerts3.body.alerts.length, 0));
const withResolved = await api('/api/alerts?resolved=true');
check('history is kept', () => assert.equal(withResolved.body.alerts.length, 1));

// 5. acknowledge
stock = 2; agent.lastHash = null;
fakeClaude.assess = async () => ({
  summary: 'Back down.', alerts: [{ severity: 'warning', category: 'inventory',
    title: 'Cedar Candle is low again', detail: 'Stock is 2.',
    recommendation: 'Reorder.', subject: 'Cedar Candle' }],
  usage: { input: 900, output: 60, cached: 800, cost: 0.003 },
});
await api('/api/agent/run', { method: 'POST' });
const open = await api('/api/alerts');
const ack = await api(`/api/alerts/${open.body.alerts[0].id}/acknowledge`, { method: 'POST' });
check('acknowledging works', () => assert.equal(ack.status, 200));

// 6. asking a question
const asked = await api('/api/agent/ask', {
  method: 'POST', body: JSON.stringify({ question: 'how is stock?' }) });
check('the agent answers a question', () =>
  assert.match(asked.body.answer, /how is stock\?/));
const empty = await api('/api/agent/ask', { method: 'POST', body: JSON.stringify({ question: '' }) });
check('an empty question is rejected', () => assert.equal(empty.status, 400));

// 7. status, activity, dashboard
const status = await api('/api/status');
check('status reports the model', () => assert.equal(status.body.model, 'claude-opus-5'));
check('status reports spend', () => assert.ok(status.body.spend24h.cost > 0));
const activity = await api('/api/activity');
check('activity is logged', () => assert.ok(activity.body.activity.length > 0));
const page = await fetch(base + '/');
check('the dashboard is served', async () => assert.equal(page.status, 200));
check('the dashboard html is real', () => assert.ok(page.status === 200));
const appjs = await fetch(base + '/app.js');
check('the dashboard script is served', () => assert.equal(appjs.status, 200));

server.close();
console.log(failures ? `\n${failures} failure(s)` : '\nall checks passed');
process.exit(failures ? 1 : 0);

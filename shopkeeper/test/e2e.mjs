/* Two halves.

   First the rules, driven directly with synthetic stores: this is where the
   behaviour now lives, so it is where the coverage belongs.

   Then the whole server, booted for real against a fake Shopify and driven
   over HTTP exactly as the dashboard does -- proving the wiring: dedup,
   resolution, acknowledgement, the API and the static files. Nothing here
   reaches the network and nothing costs money. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { buildApp } from '../server/src/index.js';
import { openDatabase, createStore } from '../database/db.js';
import { Agent } from '../server/src/agent.js';
import { Shopify } from '../server/src/lib/shopify.js';
import { RulesAgent } from '../server/src/lib/rules.js';
import { gather } from '../server/src/lib/conditions.js';

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log(`  ok    ${name}`); }
  catch (e) { failures += 1; console.log(`  FAIL  ${name}\n        ${e.message}`); }
};
const section = (name) => console.log(`\n${name}`);

const config = {
  port: 0, databaseFile: null,
  shopName: 'demo-shop', shopifyAccessToken: 'shpat_x', shopifyApiVersion: '2026-01',
  intervalMinutes: 30, runOnStart: false, dailySummary: true,
  lowStockThreshold: 5, highValueOrder: 250, refundRateThreshold: 10,
  salesDropThreshold: 40, orderLookbackDays: 7,
};

// ---------------------------------------------------------------- fixtures
const hoursAgo = (h) => new Date(Date.now() - h * 3_600_000).toISOString();

const product = ({ title = 'Cedar Candle', sku = 'CDL-01', stock = 20,
                   tracked = true, status = 'ACTIVE', id = 1 } = {}) => ({
  id: `gid://shopify/Product/${id}`, title, handle: title.toLowerCase(), status,
  totalInventory: stock,
  variants: { nodes: [{
    id: `gid://shopify/ProductVariant/${id}`, sku, price: '24.00',
    inventoryQuantity: stock,
    inventoryItem: { tracked, requiresShipping: true },
  }] },
});

const order = ({ name = '#1001', total = 40, refunded = 0, hours = 1,
                 financial = 'PAID', fulfillment = 'FULFILLED' } = {}) => ({
  id: `gid://shopify/Order/${name.slice(1)}`, name, createdAt: hoursAgo(hours),
  displayFinancialStatus: financial, displayFulfillmentStatus: fulfillment,
  currentTotalPriceSet: { shopMoney: { amount: String(total), currencyCode: 'USD' } },
  totalRefundedSet: { shopMoney: { amount: String(refunded) } },
  customer: { numberOfOrders: 1 },
  lineItems: { nodes: [{ title: 'Cedar Candle', quantity: 1, sku: 'CDL-01' }] },
});

const shop = { name: 'Demo Shop', currencyCode: 'USD' };

/** Facts for a synthetic store, through the real conditions code. */
const factsFor = (products, orders, previousInventory = {}) =>
  gather({ shop, products, orders, previousInventory, config }).facts;

const rules = new RulesAgent({ config });
const assess = async (products, orders, previous) =>
  (await rules.assess(factsFor(products, orders, previous))).alerts;
const find = (alerts, title) => alerts.find((a) => a.title === title);

// ------------------------------------------------------------- the rules
section('rules');

{
  const alerts = await assess([product({ stock: 2 })], [order()]);
  const low = find(alerts, 'Low stock');
  check('low stock alerts', () => assert.ok(low));
  check('low stock is a warning', () => assert.equal(low.severity, 'warning'));
  check('the detail carries the real number', () =>
    assert.match(low.detail, /2 units left/));
  check('the detail names the threshold', () => assert.match(low.detail, /threshold is 5/));
  check('the subject is the product', () => assert.equal(low.subject, 'Cedar Candle'));
}

{
  const alerts = await assess([product({ stock: 0 })], [order()]);
  const out = find(alerts, 'Out of stock');
  check('zero stock is out of stock, not low', () => assert.ok(out));
  check('out of stock is critical', () => assert.equal(out.severity, 'critical'));
}

{
  // Falling by more than what is left: gone before the next pass.
  const previous = { 'gid://shopify/ProductVariant/1': 12 };
  const alerts = await assess([product({ stock: 4 })], [order()], previous);
  const low = find(alerts, 'Low stock');
  check('a fast drop escalates to critical', () => assert.equal(low.severity, 'critical'));
  check('the drop is quantified', () => assert.match(low.detail, /Down 8 since/));
}

{
  const alerts = await assess([product({ stock: 50, tracked: false })], [order()]);
  check('untracked stock never alerts', () => assert.ok(!find(alerts, 'Low stock')));
  check('untracked stock never reads as out of stock', () =>
    assert.ok(!find(alerts, 'Out of stock')));
}

{
  const alerts = await assess([product({ status: 'DRAFT' })], [order()]);
  const none = find(alerts, 'No products are published');
  check('an all-draft catalog is critical', () => assert.equal(none?.severity, 'critical'));
}

{
  // 30 refunded out of 200 is 15%, above the 10% threshold but below 2x.
  const alerts = await assess([product()], [
    order({ name: '#1', total: 100 }),
    order({ name: '#2', total: 100, refunded: 30 }),
  ]);
  const refunds = find(alerts, 'Refund rate is above your threshold');
  check('a high refund rate alerts', () => assert.ok(refunds));
  check('15% is a warning, not yet critical', () => assert.equal(refunds.severity, 'warning'));
  check('the rate is quoted', () => assert.match(refunds.detail, /15% of the last 7 days/));
}

{
  // 50% is five times the threshold: past 2x, so critical.
  const alerts = await assess([product()], [order({ total: 100, refunded: 50 })]);
  check('a very high refund rate is critical', () =>
    assert.equal(find(alerts, 'Refund rate is above your threshold').severity, 'critical'));
}

{
  const alerts = await assess([product()], [order({ total: 10 })]);
  check('a clean week raises no refund alert', () =>
    assert.ok(!find(alerts, 'Refund rate is above your threshold')));
}

{
  // 600 over the prior 6 days is 100/day; 20 today is an 80% drop.
  const alerts = await assess([product()], [
    order({ name: '#today', total: 20, hours: 2 }),
    order({ name: '#prior', total: 600, hours: 72 }),
  ]);
  const drop = find(alerts, 'Sales are down against the past week');
  check('a sales collapse alerts', () => assert.ok(drop));
  check('the drop percentage is quoted', () => assert.match(drop.detail, /down 80%/));
}

{
  const alerts = await assess([product()], [order({ total: 900 })]);
  const big = find(alerts, 'A high-value order came in');
  check('a high-value order is noticed', () => assert.ok(big));
  check('and is only info', () => assert.equal(big.severity, 'info'));
  check('the amount is formatted as money', () => assert.match(big.detail, /\$900/));
}

{
  const alerts = await assess([product()], [
    order({ name: '#2001', total: 75, financial: 'PENDING' }),
  ]);
  const unpaid = find(alerts, 'Order has not been paid');
  check('an unpaid order alerts', () => assert.ok(unpaid));
  check('the order name is the subject, so it clears on its own', () =>
    assert.equal(unpaid.subject, '#2001'));
  check('the status is readable', () => assert.match(unpaid.detail, /is pending for \$75/));
}

{
  const alerts = await assess([product()], [
    order({ name: '#3001', fulfillment: 'UNFULFILLED' }),
  ]);
  const waiting = find(alerts, 'Orders are waiting to be fulfilled');
  check('unfulfilled orders alert', () => assert.ok(waiting));
  check('one unfulfilled order is only info', () => assert.equal(waiting.severity, 'info'));
  check('the count is in the detail, never the title', () => {
    assert.ok(!/\d/.test(waiting.title));
    assert.match(waiting.detail, /1 order in the last 7 days/);
  });
}

{
  const alerts = await assess([product()], Array.from({ length: 6 }, (_, i) =>
    order({ name: `#40${i}`, fulfillment: 'UNFULFILLED' })));
  check('a backlog of unfulfilled orders escalates', () =>
    assert.equal(find(alerts, 'Orders are waiting to be fulfilled').severity, 'warning'));
}

{
  const alerts = await assess([product()], [order({ total: 30 })]);
  const daily = find(alerts, 'Daily summary');
  check('the daily summary is produced', () => assert.ok(daily));
  check('it is dated, so there is one per day', () =>
    assert.match(daily.subject, /^\d{4}-\d{2}-\d{2}$/));
  check('it reports both windows', () => {
    assert.match(daily.detail, /1 order worth \$30 in the last 24 hours/);
    assert.match(daily.detail, /Past 7 days/);
  });

  const quiet = new RulesAgent({ config: { ...config, dailySummary: false } });
  const without = (await quiet.assess(factsFor([product()], [order()]))).alerts;
  check('and can be switched off', () => assert.ok(!find(without, 'Daily summary')));
}

{
  const healthy = await assess([product({ stock: 40 })], [order({ total: 30 })]);
  const loud = healthy.filter((a) => a.severity !== 'info');
  check('a healthy store raises nothing above info', () =>
    assert.deepEqual(loud.map((a) => a.title), []));
}

{
  const facts = factsFor([product({ stock: 2 })], [order()]);
  const twice = await Promise.all([rules.assess(facts), rules.assess(facts)]);
  check('the same facts give byte-identical alerts', () =>
    assert.deepEqual(twice[0].alerts, twice[1].alerts));
}

// ------------------------------------------------------------ asking it
section('questions');

{
  const facts = factsFor([product({ stock: 2 })], [order({ total: 120 })]);
  const ask = async (q) => (await rules.ask(q, facts)).answer;

  const stock = await ask('what is running low?');
  check('a stock question lists the item and its count', () =>
    assert.match(stock, /CDL-01: 2/));

  const sales = await ask('how much money did I make today?');
  check('a sales question gives both windows', () => {
    assert.match(sales, /Last 24 hours: 1 order worth \$120/);
    assert.match(sales, /Last 7 days/);
  });

  const refunds = await ask('any refunds?');
  check('a refund question with no refunds says so', () =>
    assert.match(refunds, /No refunds in the last 7 days/));

  const catalog = await ask('how many products do I have?');
  check('a catalog question counts them', () => assert.match(catalog, /1 product \(1 active\)/));

  const attention = await ask('what needs attention?');
  check('an attention question summarises', () => assert.match(attention, /1 item low on stock/));

  const help = await ask('help');
  check('help lists the topics', () => assert.match(help, /stock.*sales.*refunds/s));

  const unknown = await ask('should I rebrand to emphasise sustainability?');
  check('an unknown question admits it rather than guessing', () =>
    assert.match(unknown, /do not understand/));
  check('and says why, then offers what it does know', () => {
    assert.match(unknown, /without an ai model/i);
    assert.match(unknown, /Try asking about/);
  });
}

{
  const digital = factsFor([product({ stock: 0, tracked: false })], []);
  const answer = (await rules.ask('what is low on stock?', digital)).answer;
  check('a digital-only store gets a true answer about stock, not an empty list', () =>
    assert.match(answer, /Nothing in this store tracks inventory/));
}

// --------------------------------------------------------- the whole app
section('server');

const DB = '/tmp/shopkeeper-e2e.db';
for (const f of [DB, `${DB}-wal`, `${DB}-shm`]) fs.rmSync(f, { force: true });

let stock = 2;
let orders = [order({ total: 40 })];
const shopifyFetch = async (_url, options) => {
  const { query } = JSON.parse(options.body);
  const page = (key, nodes) => ({
    [key]: {
      edges: nodes.map((node, i) => ({ cursor: `c${i}`, node })),
      pageInfo: { hasNextPage: false, endCursor: 'c0' },
    },
  });
  const data = query.includes('shop {')
    ? { shop: { name: 'Demo Shop', myshopifyDomain: 'demo-shop.myshopify.com',
                currencyCode: 'USD', ianaTimezone: 'UTC' } }
    : query.includes('products(')
      ? page('products', [product({ stock })])
      : page('orders', orders);
  return { ok: true, status: 200, headers: new Map(), json: async () => ({ data }) };
};

const store = createStore(openDatabase(DB));
const shopify = new Shopify({ shopName: 'demo-shop', accessToken: 'x', fetchImpl: shopifyFetch });
const agent = new Agent({ config: { ...config, databaseFile: DB }, store, shopify });
const app = buildApp({ config, store, agent });
const server = app.listen(0);
const base = `http://127.0.0.1:${server.address().port}`;
const api = async (p, o) => {
  const r = await fetch(base + p, { headers: { 'Content-Type': 'application/json' }, ...o });
  return { status: r.status, body: await r.json() };
};

const run1 = await api('/api/agent/run', { method: 'POST' });
check('a manual run succeeds', () => assert.equal(run1.status, 200));
check('it creates the alerts the rules found', () => assert.equal(run1.body.created, 2));
check('and reports a summary', () => assert.match(run1.body.summary, /to look at/));

const alerts1 = await api('/api/alerts');
check('the alerts are served', () => assert.equal(alerts1.body.alerts.length, 2));
check('the low-stock one came through', () =>
  assert.ok(alerts1.body.alerts.some((a) => a.title === 'Low stock')));
check('counts are right', () => assert.equal(alerts1.body.counts.warning, 1));

const run2 = await api('/api/agent/run', { method: 'POST' });
check('a repeat pass creates no duplicate', () => assert.equal(run2.body.created, 0));
check('and reports that nothing moved', () => assert.equal(run2.body.unchanged, true));
const alerts2 = await api('/api/alerts');
check('the feed did not grow', () => assert.equal(alerts2.body.alerts.length, 2));

stock = 90;
const run3 = await api('/api/agent/run', { method: 'POST' });
check('restocking resolves the low-stock alert', () => assert.equal(run3.body.resolved, 1));
const alerts3 = await api('/api/alerts');
check('it is gone from the open list', () =>
  assert.ok(!alerts3.body.alerts.some((a) => a.title === 'Low stock')));
const withResolved = await api('/api/alerts?resolved=true');
check('but kept in history', () =>
  assert.ok(withResolved.body.alerts.some((a) => a.title === 'Low stock')));

stock = 0;
const run4 = await api('/api/agent/run', { method: 'POST' });
check('selling out raises a fresh critical', () => assert.equal(run4.body.created, 1));
const alerts4 = await api('/api/alerts?severity=critical');
check('and it is served as critical', () =>
  assert.equal(alerts4.body.alerts[0].title, 'Out of stock'));

const ack = await api(`/api/alerts/${alerts4.body.alerts[0].id}/acknowledge`, { method: 'POST' });
check('acknowledging works', () => assert.equal(ack.status, 200));
const missing = await api('/api/alerts/999999/acknowledge', { method: 'POST' });
check('acknowledging a nonexistent alert 404s', () => assert.equal(missing.status, 404));

const asked = await api('/api/agent/ask', {
  method: 'POST', body: JSON.stringify({ question: 'what is out of stock?' }) });
check('the agent answers over HTTP', () => assert.match(asked.body.answer, /CDL-01: 0/));
const empty = await api('/api/agent/ask', { method: 'POST', body: JSON.stringify({ question: '' }) });
check('an empty question is rejected', () => assert.equal(empty.status, 400));

const status = await api('/api/status');
check('status reports the interval', () => assert.equal(status.body.intervalMinutes, 30));
check('status reports alert counts', () => assert.ok(status.body.alerts.critical >= 1));
check('status no longer mentions a model', () =>
  assert.equal(status.body.model, undefined));

const activity = await api('/api/activity');
check('activity is logged', () => assert.ok(activity.body.activity.length > 0));
check('the question was logged too', () =>
  assert.ok(activity.body.activity.some((a) => a.kind === 'prompt')));

const page = await fetch(base + '/');
const html = await page.text();
check('the dashboard is served', () => assert.equal(page.status, 200));
check('and is the real page', () => assert.match(html, /<title>/i));
const appjs = await fetch(base + '/app.js');
check('the dashboard script is served', () => assert.equal(appjs.status, 200));

server.close();
console.log(failures ? `\n${failures} failure(s)` : '\nall checks passed');
process.exit(failures ? 1 : 0);

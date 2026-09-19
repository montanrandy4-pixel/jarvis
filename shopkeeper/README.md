# Shopkeeper

An AI agent that watches a Shopify store and a dashboard that shows what it
found. The agent wakes on a timer, pulls the store's current state from the
Shopify Admin API, asks Claude what — if anything — a shop owner would want to
know about it, and records the answer as alerts you can read, acknowledge and
argue with.

It is a **read-only** watcher. It has opinions; it does not have write scopes.
Nothing here edits products, cancels orders, refunds anyone or changes prices.

```
shopkeeper/
├── server/          Express API, Shopify client, Claude client, the agent loop
│   └── src/
│       ├── index.js         app wiring + startup
│       ├── agent.js         the loop: gather facts → ask Claude → store alerts
│       ├── routes.js        the HTTP surface
│       ├── config.js        environment → typed config, with validation
│       └── lib/
│           ├── shopify.js     Admin GraphQL client (cost-aware, retrying)
│           ├── conditions.js  turns raw store data into the facts Claude sees
│           └── claude.js      the model call, structured output, cost tracking
├── client/          The dashboard. Plain HTML + Tailwind, no build step.
├── database/        SQLite schema and the query layer
└── test/e2e.mjs     End-to-end test against a fake Shopify and a fake Claude
```

## Running it locally

**You need Node 20 or newer.** (`node --version`)

### 1. Install

```bash
cd shopkeeper
npm install
```

One `npm install` at the root covers everything. `server/package.json` and
`client/package.json` exist to describe those directories — they hold no
dependencies of their own, so there is a single `node_modules` and a single
lockfile. The client has no build step at all: Tailwind loads from its CDN and
the backend serves `client/` as static files.

### 2. Configure

```bash
cp .env.example .env
```

Then fill in three things.

**`SHOP_NAME`** — either `my-store` or `my-store.myshopify.com`; both work.

**`SHOPIFY_ACCESS_TOKEN`** — from a custom app on your own store:

1. Shopify admin → **Settings** → **Apps and sales channels** → **Develop apps**
2. **Create an app**, name it anything
3. **Configure Admin API scopes** → tick `read_products`, `read_inventory`,
   `read_orders` — those three and nothing else; the agent never writes
4. **Install app**, then **Reveal token once** and copy the `shpat_…` value

The token is shown exactly once. If you lose it, uninstall and reinstall the
app to get a new one.

**`ANTHROPIC_API_KEY`** — from https://console.anthropic.com/settings/keys.
This is what costs money. See *What it costs* below.

Everything else in `.env.example` has a working default.

### 3. Run

```bash
npm start           # or: npm run dev   (restarts on file changes)
```

Open **http://localhost:3000**.

The server refuses to start with a missing or malformed config and tells you
which key is wrong, rather than failing later with a confusing 401 from
Shopify.

### 4. Test

```bash
npm test
```

21 checks. This boots the real Express app with a **fake** Shopify and a
**fake** Claude, then drives it over HTTP exactly as the dashboard does. It
proves the wiring — routing, dedup, resolution, SSE, the database — and costs
nothing. It does not prove the model behaves well, because no model is called.

## What the agent actually does

Each pass:

1. **Gather.** One GraphQL call for products and inventory, one for orders
   since `ORDER_LOOKBACK_DAYS` ago.
2. **Reduce.** `conditions.js` turns that into a small facts object: what's
   below `LOW_STOCK_THRESHOLD`, revenue and order count over 24h and 7d, the
   7-day refund rate, orders that are unpaid or unfulfilled, and the catalog.
   This is deterministic — the thresholds are yours, not the model's.
3. **Hash.** If the facts hash matches the previous pass and the trigger was
   the timer, **the model is not called.** A quiet shop costs nothing to watch.
   `AGENT_SKIP_UNCHANGED=false` turns this off for debugging. A manual run from
   the dashboard always calls the model.
4. **Assess.** The facts go to Claude with a system prompt describing what a
   shop owner cares about. The response comes back as structured output — a
   list of `{title, severity, detail, suggested_action}` — not free text to be
   parsed with a regex.
5. **Reconcile.** Each alert gets a fingerprint from its *stable* fields, so
   the model rephrasing "stock is low" doesn't create a second alert. New
   fingerprints are inserted; open alerts the model no longer reports are
   marked resolved. Dedup is a partial unique index in SQLite, not a
   read-then-write in JavaScript, so concurrent passes can't race.

The loop never dies on error. A failed pass is logged, emitted to the
dashboard, and the next one runs on schedule.

## What it costs

Two levers, both on by default:

- **Skip unchanged passes.** A store with no new orders and no inventory
  movement produces an identical facts hash, and the pass ends before the SDK
  is touched. On a slow day the agent can run 48 times and call the model
  twice.
- **Prompt caching.** The system prompt is sent with `cache_control`, so the
  fixed instructions are billed at the cached rate after the first call.

`GET /api/status` reports `spend24h` — what the last 24 hours of passes cost, computed from real token counts in
each response's `usage`, at the published per-million rates for the model in
`CLAUDE_MODEL`. Lower the bill further by raising `AGENT_INTERVAL_MINUTES` or
setting `CLAUDE_MODEL=claude-sonnet-5`.

## API

Everything is under `/api`.

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/health` | liveness, uptime |
| `GET` | `/api/status` | agent running? last run, next run, model, 24h spend |
| `GET` | `/api/alerts` | `?limit=`, `?severity=`, `?resolved=true` |
| `POST` | `/api/alerts/:id/acknowledge` | mark one read |
| `GET` | `/api/activity` | recent activity plus the last 20 runs |
| `POST` | `/api/agent/run` | force a pass now |
| `POST` | `/api/agent/ask` | `{"question": "..."}` → `{answer, usage}` |
| `POST` | `/api/agent/start` | start the timer |
| `POST` | `/api/agent/stop` | stop the timer |
| `GET` | `/api/events` | SSE stream: `alert`, `resolved`, `run:start`, `run:end`, `run:error` |

```bash
curl -s localhost:3000/api/status | jq
curl -s -X POST localhost:3000/api/agent/run | jq
curl -s -X POST localhost:3000/api/agent/ask \
  -H 'content-type: application/json' \
  -d '{"question":"which product is closest to selling out?"}' | jq -r .answer
```

`/api/events` keeps itself alive with a comment frame every 15 seconds,
because proxies and browsers hang up on a silent stream. The dashboard
reconnects on its own if the server restarts.

## The database

SQLite, WAL mode, at `DATABASE_FILE` (default `database/shopkeeper.db`). Four
tables: `runs` (every pass, with token counts), `alerts`, `inventory_snapshots`
(so a later pass can see what moved), and `activity` (the feed, including your
questions and the agent's answers).

It is a plain file. Delete it to start clean; the schema is recreated on boot.
`database/*.db*` is gitignored.

## Honest limits

- **No model call has ever been made from this app, and no real store has ever
  been read by it.** The e2e test uses a fake Shopify and a fake Claude. The
  Shopify queries and the Anthropic call were written against the current API
  and SDK — structured output here is `client.beta.messages.parse` with
  `betaZodOutputFormat`, which is where it lives in SDK 0.71 — but the first
  run against your real credentials is the first real run.
- **It only reads.** Any "suggested action" in an alert is for you to carry
  out. Nothing in this codebase can change your store.
- **Thresholds are blunt.** `LOW_STOCK_THRESHOLD` is one number for every
  product. A store where one SKU sells 50/day and another sells 1/month wants
  two different numbers; this has one.
- **Digital products have no inventory.** If your catalog is all digital goods
  with inventory tracking off, low-stock alerts will never fire — that is
  correct, not broken.
- **Single process.** The timer lives in the process. Run one instance.

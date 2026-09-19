# Shopkeeper

An agent that watches a Shopify store and a dashboard that shows what it found.
The agent wakes on a timer, pulls the store's current state from the Shopify
Admin API, runs a set of rules over it, and records anything worth knowing as
alerts you can read, acknowledge and argue with.

**No AI, no API key, no running cost.** Every alert comes from a rule you can
read in `server/src/lib/rules.js` and predict the output of. The only account
you need is your own Shopify store.

It is also **read-only**. It has opinions; it does not have write scopes.
Nothing here edits products, cancels orders, refunds anyone or changes prices.

```
shopkeeper/
├── server/          Express API, Shopify client, the rules, the agent loop
│   └── src/
│       ├── index.js         app wiring + startup
│       ├── agent.js         the loop: gather facts → run rules → store alerts
│       ├── routes.js        the HTTP surface
│       ├── config.js        environment → typed config, with validation
│       └── lib/
│           ├── shopify.js     Admin GraphQL client (cost-aware, retrying)
│           ├── conditions.js  turns raw store data into a page of facts
│           └── rules.js       every alert this thing can raise, and the answers
├── client/          The dashboard. Plain HTML + Tailwind, no build step.
├── database/        SQLite schema and the query layer
└── test/e2e.mjs     70 checks: the rules directly, then the whole server
```

## Running it locally

**You need Node 20 or newer.** Get the LTS build from https://nodejs.org if you
don't have it. (`node --version` to check.)

### The short way

```bash
cd shopkeeper
node setup.mjs
```

That checks your Node version, installs dependencies, asks for your store name
and access token, writes `.env` with permissions `600`, and offers to start the
server.
It is safe to run twice — it never overwrites an answer you already gave, and
skips anything already done. If you'd rather do it by hand, the same steps are
below.

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

Then fill in two things.

**`SHOP_NAME`** — either `my-store` or `my-store.myshopify.com`; both work.

**`SHOPIFY_ACCESS_TOKEN`** — from a custom app on your own store:

1. Shopify admin → **Settings** → **Apps and sales channels** → **Develop apps**
2. **Create an app**, name it anything
3. **Configure Admin API scopes** → tick `read_products`, `read_inventory`,
   `read_orders` — those three and nothing else; the agent never writes
4. **Install app**, then **Reveal token once** and copy the `shpat_…` value

The token is shown exactly once. If you lose it, uninstall and reinstall the
app to get a new one.

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

70 checks, in two halves. The first drives the rules directly against synthetic
stores — every alert, both severities of each, and the cases that must *not*
alert. The second boots the real Express app against a fake Shopify and drives
it over HTTP as the dashboard does, covering dedup, resolution, acknowledgement,
the API and the static files. Nothing touches the network.

## What the agent actually does

Each pass:

1. **Gather.** One GraphQL call for products and inventory, one for orders
   since `ORDER_LOOKBACK_DAYS` ago.
2. **Reduce.** `conditions.js` turns that into a small facts object: what's
   below `LOW_STOCK_THRESHOLD`, revenue and order count over 24h and 7d, the
   7-day refund rate, orders that are unpaid or unfulfilled, and the catalog.
3. **Judge.** `rules.js` turns facts into alerts. The whole list:

| Alert | Fires when | Severity |
|---|---|---|
| Out of stock | a tracked variant is at 0 | critical |
| Low stock | at or below `LOW_STOCK_THRESHOLD` | warning, or critical if it fell by more than is left |
| No products are published | every product is draft or archived | critical |
| The store has no products | Shopify returned an empty catalog | critical |
| Refund rate above threshold | 7-day refunds exceed `REFUND_RATE_THRESHOLD`% | warning, critical past double |
| Sales are down | last 24h is `SALES_DROP_THRESHOLD`% below the prior 6-day daily average | warning |
| A high-value order came in | largest 24h order ≥ `HIGH_VALUE_ORDER` | info |
| Order has not been paid | an order isn't paid or refunded | warning |
| Orders waiting to be fulfilled | any unfulfilled | info, warning at 5+ |
| Daily summary | once a day | info |

4. **Reconcile.** Each alert gets a fingerprint from its *stable* fields —
   category, subject, title — never the detail. Stock falling from 4 to 3
   updates nothing and re-alerts nobody; it is the same condition. New
   fingerprints are inserted, and open alerts the rules no longer produce are
   marked resolved. Dedup is a partial unique index in SQLite, not a
   read-then-write in JavaScript, so concurrent passes can't race.

Because the rules are pure, the same store always produces the same alerts.
That is what makes step 4 work.

The loop never dies on error. A failed pass is logged, emitted to the
dashboard, and the next one runs on schedule.

## Asking it things

The dashboard has an ask box. Without a model behind it, it understands a fixed
set of topics rather than anything you can type:

| Ask about | You get |
|---|---|
| `stock`, `inventory`, `low`, `out` | what is low or out, with counts |
| `sales`, `revenue`, `orders`, `today` | 24h and 7d figures, and the trend |
| `refunds` | refund total and rate against your threshold |
| `products`, `catalog` | counts, how many active, how many out of stock |
| `attention`, `problems`, `status` | everything currently worth a look |
| `help` | the list above |

Anything it doesn't recognise gets an honest "I don't understand that one"
and the list — it never guesses. If you want it to answer arbitrary questions,
that needs a model, and this build deliberately doesn't have one.

## API

Everything is under `/api`.

| Method | Path | Does |
|---|---|---|
| `GET` | `/api/health` | liveness, uptime |
| `GET` | `/api/status` | agent running? last run, next run, alert counts |
| `GET` | `/api/alerts` | `?limit=`, `?severity=`, `?resolved=true` |
| `POST` | `/api/alerts/:id/acknowledge` | mark one read |
| `GET` | `/api/activity` | recent activity plus the last 20 runs |
| `POST` | `/api/agent/run` | force a pass now |
| `POST` | `/api/agent/ask` | `{"question": "..."}` → `{answer}` |
| `POST` | `/api/agent/start` | start the timer |
| `POST` | `/api/agent/stop` | stop the timer |
| `GET` | `/api/events` | SSE stream: `alert`, `resolved`, `run:start`, `run:end`, `run:error` |

```bash
curl -s localhost:3000/api/status | jq
curl -s -X POST localhost:3000/api/agent/run | jq
curl -s -X POST localhost:3000/api/agent/ask \
  -H 'content-type: application/json' \
  -d '{"question":"what is low on stock?"}' | jq -r .answer
```

`/api/events` keeps itself alive with a comment frame every 15 seconds,
because proxies and browsers hang up on a silent stream. The dashboard
reconnects on its own if the server restarts.

## The database

SQLite, WAL mode, at `DATABASE_FILE` (default `database/shopkeeper.db`). Four
tables: `runs` (every pass), `alerts`, `inventory_snapshots`
(so a later pass can see what moved), and `activity` (the feed, including your
questions and the agent's answers).

It is a plain file. Delete it to start clean; the schema is recreated on boot.
`database/*.db*` is gitignored.

## Honest limits

- **It has been run against one real store, not against real traffic.** The
  Admin API queries were executed against a live shop (14 digital products, no
  orders yet) and the whole app — agent loop, rules, SQLite, HTTP API, SSE,
  dashboard — was driven with that payload. So the queries are known to be
  valid and the wiring is known to hold. What has *not* been exercised is a
  store with orders in it: refunds, unpaid orders, sales drops and the
  fulfilment backlog are covered by the test suite's synthetic stores only.
- **It only reads.** Any "recommendation" in an alert is for you to carry out.
  Nothing in this codebase can change your store.
- **The rules are blunt, on purpose.** `LOW_STOCK_THRESHOLD` is one number for
  every product. A store where one SKU sells 50/day and another sells 1/month
  wants two different numbers; this has one. A model would weigh that; rules
  don't. That is the trade you made by not wanting an AI in the loop, and it is
  usually the right one — you can read every decision this thing makes.
- **The ask box is keyword matching**, not comprehension. See above.
- **Digital products have no inventory.** If your catalog is all digital goods
  with tracking off, stock alerts will never fire — that is correct, not
  broken, and asking about stock says so.
- **Single process.** The timer lives in the process. Run one instance.

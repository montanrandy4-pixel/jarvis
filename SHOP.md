# Shop autopilot

A whole Shopify store as a file, and the tooling to build and run it: brand,
collections, pages, policies, navigation and catalogue in `store.toml`, created
in your store with one command, then kept in step with your supplier's feed.
Product copy is written by the same local model JARVIS uses, so there is no
second API bill and nothing leaves the machine.

```bash
shop scaffold          # a complete store, ready to edit
shop preview --open    # see the whole thing before it exists
shop doctor            # check the token, the store and the feed
shop build --live      # collections, pages, policies, navigation
shop apply --live      # the products
shop run --live        # keep it in step, every hour
```

`shop scaffold` writes three files:

| File | What it is |
|---|---|
| `store.toml` | The store itself: brand, collections, pages, policies, navigation |
| `feed.csv` | The catalogue, in the shape a supplier feed arrives in |
| `shop.toml` | How it runs: pricing rules, order handling, schedule |

Edit those three and you have your shop rather than the example one. `shop
preview` renders all of it — home page, every collection, every product, every
policy — as a local static site you can read before any of it is real.

## The one step you have to do yourself

**Creating the Shopify account.** It needs your email, your password, your
payment details and your acceptance of Shopify's terms, so it cannot be
automated on your behalf. It takes about three minutes:

1. Sign up at [shopify.com](https://www.shopify.com) and pick a plan (there is
   a trial).
2. In the admin: **Settings → Apps and sales channels → Develop apps → Create
   an app**, with the scopes in the table below, then **Install app**.
3. Copy the Admin API access token (`shpat_…`).

```bash
export SHOPIFY_ADMIN_TOKEN=shpat_...
shop doctor && shop build --live && shop apply --live
```

Everything after that is automated.

## What it will not do

Worth reading before the rest.

- **It does not create the account.** See above — that part is yours.
- **It does not publish anything.** New products are created as **drafts**. A
  person decides what goes on sale. After that, the autopilot maintains them.
- **It does not mark orders fulfilled.** Fulfilment tells a customer their
  parcel is on its way and cannot be taken back. Orders are read, classified
  and tagged; anything unusual gets `needs-review`. `auto_fulfill` exists in
  the config and is deliberately not implemented — see *Fulfilment* below.
- **It does not invent product facts.** Generated copy is checked against what
  the supplier actually said, and rejected copy falls back to their text.

## Setup

1. **Create a custom app** in your Shopify admin (see above). Give it these
   Admin API scopes:

   | Scope | Why |
   |---|---|
   | `read_products`, `write_products` | Create and update listings |
   | `read_inventory`, `write_inventory` | Keep stock levels in step |
   | `read_locations` | Find where stock lives |
   | `read_orders`, `write_orders` | Triage and tag orders |
   | `read_content`, `write_content` | Pages and navigation |

   Install it and copy the Admin API access token (`shpat_…`).

2. **Tell the autopilot about it:**

   ```bash
   export SHOPIFY_ADMIN_TOKEN=shpat_...
   shop init            # then edit shop.toml
   shop doctor
   ```

3. **Point it at a feed.** `feed_path` takes a CSV, a JSON file, or an http(s)
   URL. Column names are matched loosely: `sku` / `Item Number` / `MPN`,
   `cost` / `Wholesale Price` / `Your Price`, `qty` / `Stock` / `In Stock`, and
   so on. `shop doctor` prints the mapping it worked out and every row it had
   to reject, with the reason.

## Building the store

`shop build` makes the store match `store.toml`. Everything is matched by
handle, so it creates what is missing and edits what has changed — running it
twice writes nothing the second time.

```
$ shop build --live
  create    collection  Lighting
  create    collection  Shelving & Storage
  create    page        About
  create    policy      refund policy
  create    menu        Main menu
  ...
13 to create, 0 to update, 0 already correct
built: {'created': 13, 'updated': 0, 'failed': 0}

$ shop build --live          # after editing one page
0 to create, 1 to update, 12 already correct
```

Collections given a `tag` build themselves: any product carrying that tag joins
automatically, including ones a later feed sync adds.

### The policies

`store.toml` ships with drafts of the refund, privacy, terms and shipping
policies, so the store is not missing legally required pages on day one. They
are full of `{{PLACEHOLDERS}}` — your business name, address, delivery prices,
jurisdiction — and `shop build` will happily publish them with the placeholders
still in, because it cannot know your details.

**They are a starting point, not legal advice.** Read them, fill them in, and
have them checked against the rules where you sell.

## How a sync works

```
feed ──► plan ──► apply
  │        │        │
  │        │        └─ only the differences are written
  │        └─ create / reprice / restock / retire / hold
  └─ parsed, validated, bad rows rejected with reasons
```

`plan` and `apply` are the same code path; `plan` simply stops before writing.
What `shop plan` prints is what `shop apply --live` will do.

Re-running is safe. The autopilot keeps a ledger of what it created
(`~/.local/state/shop/ledger.json`), matches products by SKU, and writes only
genuine differences — a second run with an unchanged feed makes zero writes.
If the ledger says a product exists but the store does not return it, the
product is **held**, not recreated: a bad read should never duplicate a catalogue.

### Pricing

```toml
[shop.pricing]
markup = 2.5              # cost x 2.5
handling = 0.0            # added after the markup
charm_ending = "0.99"     # round up to .99 (or "0.95", "5", "none")
floor = 0.0               # never sell below this
ceiling = 500.0           # hold anything above this for review
compare_at_multiplier = 0.0   # or use the supplier's RRP, when it is higher
min_change = 0.01         # ignore smaller moves, to avoid churn
```

Two prices are never published: one that would not cover the cost, and one
above the ceiling. Both are **held** and listed for you to look at.

### Orders

Every new order is read and classified, then tagged `autopilot-seen` so it is
only handled once. It is additionally tagged `needs-review` when it is at or
above `review_above`, when payment has not gone through, or when it contains a
SKU the autopilot does not manage.

```
#1001          61.99 GBP  ok
#1002         980.00 GBP  review: total 980.00 is at or above 250.0; payment is pending
```

### Fulfilment

`auto_fulfill` is in the config, defaults to `false`, and is not wired up. That
is a deliberate gap. Marking an order fulfilled emails the customer that their
order has shipped and cannot be undone, and I could not test that path against
a real store — only against a mock. Shipping the code anyway would have meant
the first time it ran for real was also the first time it was tried. If you
want it, the place to add it is `orders.py`, using `fulfillmentCreate` against
the order's fulfilment orders, and it should be tested on a development store
first.

## Product copy

With `ai_copy = true`, listings are written by the local model (whatever
`jarvis` is configured to use). The model is given only the supplier's facts
and told not to add any, and the result is rejected if it is too long, shouting,
or makes a claim the supplier never made — `FDA-approved`, `guaranteed`,
`award-winning`, `dishwasher safe` and similar. Rejected copy falls back to the
supplier's own description, so a listing is never blocked by a bad generation.

This is a guard, not a guarantee. Read the drafts before publishing them.

## Running it continuously

```bash
shop run --live --interval 60
```

Or from cron, which survives reboots:

```cron
*/30 * * * * cd /path/to/shop && SHOPIFY_ADMIN_TOKEN=shpat_... /usr/local/bin/shop apply --live >> sync.log 2>&1
```

## Asking JARVIS

When a shop is configured, JARVIS picks up three read-only tools, so you can
ask out loud: *"how's the shop doing"*, *"anything low on stock?"*, *"what
would the next sync change?"*, *"any orders needing attention?"*.

Applying a sync is not one of them. Forty catalogue changes cannot be
summarised in a sentence, so that stays a deliberate `shop apply` at a keyboard.

## What has actually been tested

Every part of this runs against a **mock Shopify Admin API** in the test suite
(106 tests): creating products, repricing, stock, retiring, pagination, user
errors, throttling and retries, order triage, dry runs, and a full sync that is
provably a no-op the second time.

It has **not** been run against a real Shopify store — I had no store or token.
The GraphQL documents are in `src/shop/queries.py`, deliberately in one small
file, so they can be checked against the API version you are on. First real run:

```bash
shop doctor                     # proves the token, store and API version
shop plan                       # read every line of it
shop apply --live --limit 1     # one product, then look at it in the admin
```

Shopify ships an API version quarterly and supports each for a year. If
`api_version` falls out of support, `shop doctor` fails with a 404 naming it.

## What has actually been verified

Both the structure build and the catalogue sync run against a **mock Shopify
Admin API** in the test suite, including the thing that matters most for
anything on a timer: a full build and a full sync are provably no-ops the
second time they run.

The preview is rendered and checked in a real browser.

It has **not** been run against a real Shopify store — I had no store or token.
The pages and menus mutations (`pageCreate`, `menuCreate`) arrived in the
2024-10 API cycle; if your `api_version` predates them, `shop build` will report
them as unknown fields rather than failing quietly.

## Files

```
src/shop/
  config.py     shop.toml, secrets from the environment
  client.py     GraphQL client: cost-aware throttling, retries, dry run
  queries.py    every GraphQL document, in one reviewable place
  feed.py       supplier feeds: CSV/JSON, loose columns, messy values
  pricing.py    cost -> price, deterministically
  catalog.py    plan and apply the difference between feed and store
  orders.py     triage and tagging
  copy.py       generated listings, and the checks on them
  ledger.py     what the autopilot has already done
  autopilot.py  one pass, and the loop
  store.py      the store definition: brand, collections, pages, policies
  scaffold.py   the starter store this all begins from
  storefront.py building the structure, idempotently
  preview.py    the whole store as a local static site
  cli.py        scaffold | preview | doctor | build | plan | apply | orders | run
```

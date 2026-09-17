# Shop autopilot

Runs a Shopify store from a supplier feed: creates listings, keeps prices and
stock in step, retires what the supplier drops, and triages incoming orders.
It writes product copy with the same local model JARVIS uses, so there is no
second API bill and nothing leaves the machine.

```bash
shop init          # write a starter shop.toml
shop doctor        # check the token, the store and the feed
shop plan          # what a sync would change -- writes nothing
shop apply --live  # do it
shop run --live    # keep doing it, every hour
```

## What it will not do

Worth reading before the rest.

- **It does not create the store.** Shopify needs your account, your plan and
  your payment details. This automates a store you own.
- **It does not publish anything.** New products are created as **drafts**. A
  person decides what goes on sale. After that, the autopilot maintains them.
- **It does not mark orders fulfilled.** Fulfilment tells a customer their
  parcel is on its way and cannot be taken back. Orders are read, classified
  and tagged; anything unusual gets `needs-review`. `auto_fulfill` exists in
  the config and is deliberately not implemented — see *Fulfilment* below.
- **It does not invent product facts.** Generated copy is checked against what
  the supplier actually said, and rejected copy falls back to their text.

## Setup

1. **Create a custom app** in your Shopify admin: Settings → Apps and sales
   channels → Develop apps → Create an app. Give it these Admin API scopes:

   | Scope | Why |
   |---|---|
   | `read_products`, `write_products` | Create and update listings |
   | `read_inventory`, `write_inventory` | Keep stock levels in step |
   | `read_locations` | Find where stock lives |
   | `read_orders`, `write_orders` | Triage and tag orders |

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
  cli.py        shop init | doctor | plan | apply | orders | status | run
```

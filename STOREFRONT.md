# Storefront agent

Runs a Shopify store that sells **files**. Companion to [`shop`](SHOP.md), not a
replacement: that one syncs a supplier feed of physical goods, this one looks
after a hand-authored digital catalogue.

```bash
storefront doctor          # token, store, and are the files where you said?
storefront audit           # is every live product actually deliverable?
storefront attach --live   # attach the files, through your own browser
storefront orders          # recent orders, and anything odd about them
storefront report          # what the shop did
storefront run --live      # keep checking, hourly
```

## The problem it exists to solve

A physical store fails loudly: stock runs out, a parcel goes missing. A digital
store fails quietly and worse — **a product goes live before its file is
attached, a customer pays, and receives nothing.** Shopify does not prevent
this, and nothing in the admin warns you about it.

`storefront audit` is the answer. Every check maps to a question a customer
asks with their money:

| Check | Severity | Why |
|---|---|---|
| Published to the Online Store | blocker | Active ≠ visible. They're different fields. |
| A digital file is recorded | blocker | Pay-and-get-nothing, the failure above |
| Variant has a SKU | blocker | No SKU means no file can ever be matched to it |
| `requiresShipping` is false | blocker | Otherwise checkout demands a postal address for a download |
| Price is set and non-zero | blocker | |
| Has a cover image | warning | An imageless product converts badly |
| Images processed cleanly | warning | A failed upload shows as a broken tile |
| Has tags | note | Smart collections won't pick it up |

Exit code is non-zero when there are blockers, so it works in a pre-launch
check or a cron job.

## Attaching files

Shopify routes digital-file uploads through the merchant's browser: the file
goes from your disk to Shopify's storage and **no Admin API sits in that
path**. That is a deliberate security boundary. No server-side script can do
this step — not this agent, not any other.

A browser on *your* machine can. `storefront attach` drives a real Chromium
through Playwright, and `set_input_files` writes straight to the file input
without ever opening an OS file dialog.

Two things stay where Shopify put them:

- **You sign in yourself.** The agent opens a browser and waits. It never
  handles your password and never touches your 2FA. The profile persists in
  `~/.local/state/storefront/browser`, so you do this once.
- **It drives a UI, and UIs move.** The selectors are a best effort against
  Shopify admin as it stands. When Shopify rearranges the page this breaks
  *loudly* and tells you which files to attach by hand. It will never silently
  skip a product and report success.

```bash
pip install playwright && playwright install chromium
storefront attach              # dry run: shows the plan, opens nothing
storefront attach --live       # opens a browser and does it
```

Anything it could not do, you do in the admin, then tell the agent:

```bash
storefront assets add SS-RATE-CALC "~/solostack/deliverables/Rate Calculator and Pricing Toolkit.xlsx"
```

## How it knows what's attached

Shopify's Digital Products app keeps attachments in its own storage, outside
the Admin API. The agent cannot ask Shopify whether a product has a file — so
it remembers, in `~/.local/state/storefront/assets.json`.

It records a SHA-256 of each file. That makes it more than a checklist: **if
you rebuild a product file and forget to re-upload it, the digest stops
matching and the audit says so.** Silently shipping last month's version to
new customers is exactly the kind of thing nobody notices for a quarter.

## Orders

Digital orders need almost nothing done to them — the app delivers on payment.
So the agent does not fulfil. It watches for the ways a *digital* order goes
wrong:

- payment authorised but never captured, so the file never went out
- a line item whose SKU has no file recorded
- an unusually large order, which on a digital store often means card testing
- a line item still marked as requiring shipping

Everything it sees once is tagged `storefront-seen`, so later runs stay quiet.
Anything concerning also gets `needs-review`.

## What it will not do

Deliberate gaps, not missing features:

- **It does not fulfil, refund, or email customers.** All irreversible or
  outward-facing. An agent doing those unattended is a liability.
- **It does not change prices or publish products.** Shopify admin is one
  click away and a human should make that call.
- **It writes nothing without `--live`.** Dry run is the default everywhere.

It looks, it tags, it tells you.

## The 24/7 watch

```bash
storefront watch --live --desktop
```

Three checks on three clocks, because they cost different amounts and change
at different speeds:

| Check | Every | Why that often |
|---|---|---|
| Catalogue | every pass | A product live without a file is the most expensive silent failure here |
| Orders | every pass | An odd payment should not wait an hour |
| Storefront (browser) | `storefront_check_minutes` | Drives a real Chromium, so it is expensive |

Nothing short of Ctrl-C stops it. A failing pass logs and waits for the next
one; a watch that dies at 3am is worse than no watch, because you believe you
are being watched. Persistent failure escalates on the 3rd, 12th and 48th
consecutive pass — once each, not every time.

## What the browser sees that the API cannot

The Admin API answers questions about *records*. It will tell you a product is
active, published and priced while the storefront itself is broken — a theme
update that swallowed the buy button, a collection page that 404s, a price
rendering as nothing, an app script throwing and taking the page with it. None
of that is a record; it is a rendering.

`storefront storefront-check` walks the real shop and checks what a customer
notices in the first thirty seconds:

- the home page loads, and is not password-protected
- collection and content pages return 200
- product pages render **an actual price**
- nothing says "sold out" (impossible on a digital store, so it means breakage)
- **add to cart works and the item reaches the cart**

It buys nothing. The deepest it goes is the cart, which is where a broken
store usually reveals itself.

```bash
storefront storefront-check          # headless
storefront storefront-check --show   # watch it happen
```

## Alerts that stay readable

An alerting system earns its keep by what it *doesn't* send. A watcher
reporting the same broken product every ten minutes trains you to ignore it,
and then the one alert that mattered lands in a muted channel.

- **Deduplicated.** Each alert has a stable key. The same problem does not
  fire again until its cooldown expires, however many passes see it.
- **Resolution is reported.** A problem that goes away says so, once, so you
  are not left wondering.
- **Routed by severity.** Critical can wake you; notes go to the log.

| Channel | Default threshold | Notes |
|---|---|---|
| Console | everything | |
| Log file | everything | `~/.local/state/storefront/alerts.log` |
| Desktop | warning and above | macOS `osascript`, Linux `notify-send` |
| Webhook | warning and above | Slack, Discord, anything taking `{"text": …}` |
| Email | critical only | SMTP; use an app password |

Channels are independent and best-effort: a dead webhook never stops a desktop
notification, and no channel failure stops the loop.

**Alerts are not written by a language model, deliberately.** They already say
exactly what is wrong and what to do — *"Rate Calculator: no digital file
recorded — attach it"*. Paraphrasing that could only make it less precise, and
would add a dependency that can fail at 3am.

## Running it as a service

macOS, `~/Library/LaunchAgents/com.solostack.watch.plist`, or Linux systemd:

```ini
[Unit]
Description=Shop watch
After=network-online.target

[Service]
Environment=SHOPIFY_ADMIN_TOKEN=shpat_...
WorkingDirectory=/home/you/jarvis
ExecStart=/usr/local/bin/storefront watch --live --desktop
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
```

## Configuration

Merge `storefront.example.toml` into your `shop.toml`. The token belongs in
the environment, never the file:

```bash
export SHOPIFY_ADMIN_TOKEN=shpat_...
storefront doctor
```

Required scopes: `read_products`, `read_orders`, `write_orders` (tagging only),
`read_inventory`, `read_publications`, `read_discounts`.

## Running it continuously

```bash
storefront run --live --interval 60
```

Or from cron, which survives reboots:

```cron
0 * * * * cd /path/to/jarvis && SHOPIFY_ADMIN_TOKEN=shpat_... storefront audit >> storefront.log 2>&1
```

A failing pass never kills the loop; it logs and waits for the next one.

## What has actually been tested

59 unit tests covering the audit rules, order triage, the asset ledger
(including digest staleness and a corrupt ledger file), and attachment
planning. The audit tests lean hard on the blocker cases, because a false "all
clear" is the one failure that costs a real customer real money.

Alert dedup is covered hard — firing once, staying quiet, reporting
resolution, surviving a restart and a corrupt state file, and not falling over
when a webhook target is dead.

**The browser half is not unit-tested** and has not been run against a live
Shopify admin or storefront — there was no browser or store session available where it was
written. `plan_from_mapping` is tested; `attach_all` is not. Treat the first
real run as a test: use `--live` on a single product, check the admin, then do
the rest.

"""Command line for the shop autopilot."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, autopilot, catalog, feed, orders
from .client import ShopifyClient, ShopifyError
from .config import Config
from .ledger import Ledger

STARTER_CONFIG = """\
# What the autopilot manages. Start with dry_run = true and read the plan.
[shop]
store_domain = "your-store.myshopify.com"
token_env = "SHOPIFY_ADMIN_TOKEN"
api_version = "2026-01"

# A CSV or JSON file, or an http(s) URL, from your supplier.
feed_path = "feed.csv"
vendor = ""
collection = ""
on_missing = "unpublish"      # or "archive"

process_orders = true
review_above = 250.0
auto_fulfill = false          # deliberately off; see the README
low_stock_threshold = 3

ai_copy = true                # write listings with the local model
brand_voice = "plain, specific, no hype"

dry_run = true                # nothing is written while this is true
interval_minutes = 60

[shop.pricing]
markup = 2.5                  # cost x 2.5
handling = 0.0
charm_ending = "0.99"
floor = 0.0
ceiling = 0.0                 # hold anything above this for review
compare_at_multiplier = 0.0
min_change = 0.01
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shop", description="Run a Shopify store on autopilot."
    )
    parser.add_argument("--version", action="version", version=f"shop {__version__}")
    parser.add_argument("--config", help="Path to shop.toml.")
    parser.add_argument("--feed", help="Override the feed path or URL.")
    parser.add_argument("--debug", action="store_true", help="Verbose logging.")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("init", help="Write a starter shop.toml.")

    scaffold = subs.add_parser(
        "scaffold", help="Write a whole store: store.toml, feed.csv, shop.toml."
    )
    scaffold.add_argument("--force", action="store_true",
                          help="Overwrite files that already exist.")
    scaffold.add_argument("--into", default=".", help="Where to write them.")

    build = subs.add_parser(
        "build", help="Make the store match store.toml (collections, pages, "
                      "policies, navigation)."
    )
    build.add_argument("--live", action="store_true", help="Write to the store.")
    build.add_argument("--store", default="store.toml", help="Store definition.")

    preview = subs.add_parser(
        "preview", help="Render the store locally, before it exists."
    )
    preview.add_argument("--store", default="store.toml", help="Store definition.")
    preview.add_argument("--out", default="preview", help="Directory to write.")
    preview.add_argument("--open", action="store_true", help="Open it in a browser.")
    subs.add_parser("doctor", help="Check the config, token and feed.")
    subs.add_parser("plan", help="Show what a sync would change. Writes nothing.")

    apply_cmd = subs.add_parser("apply", help="Carry out the plan.")
    apply_cmd.add_argument(
        "--live",
        action="store_true",
        help="Actually write to the store (otherwise this is a dry run).",
    )
    apply_cmd.add_argument(
        "--limit", type=int, default=0, help="Only apply the first N actions."
    )

    order_cmd = subs.add_parser("orders", help="Triage recent orders.")
    order_cmd.add_argument("--live", action="store_true", help="Apply the tags.")
    order_cmd.add_argument("--since", default="", help="ISO date to look back to.")

    subs.add_parser("status", help="What the autopilot has done so far.")

    run_cmd = subs.add_parser("run", help="Keep syncing on a schedule.")
    run_cmd.add_argument("--live", action="store_true", help="Write to the store.")
    run_cmd.add_argument("--interval", type=int, help="Minutes between passes.")
    return parser


def _config(args, *, live: bool | None = None) -> Config:
    config = Config.load(
        Path(args.config) if args.config else None,
        feed_path=getattr(args, "feed", None),
        interval_minutes=getattr(args, "interval", None),
    )
    if live is not None:
        config.dry_run = not live
    return config


def cmd_init(args) -> int:
    path = Path(args.config or "shop.toml")
    if path.exists():
        print(f"{path} already exists; leaving it alone.")
        return 1
    path.write_text(STARTER_CONFIG)
    print(f"Wrote {path}.\n\nNext:")
    print("  1. Put your store domain in it.")
    print("  2. Create a custom app in Shopify admin (Settings > Apps and sales")
    print("     channels > Develop apps), give it read/write products,")
    print("     inventory and orders, and install it.")
    print("  3. export SHOPIFY_ADMIN_TOKEN=shpat_...")
    print("  4. shop doctor")
    return 0


def cmd_scaffold(args) -> int:
    from . import scaffold as scaffold_module

    written = scaffold_module.write(args.into, force=args.force)
    if not written:
        print("Everything already exists here. Use --force to overwrite.")
        return 1
    for path in written:
        print(f"wrote {path}")
    print("\nThat is a complete store: brand, collections, pages, policies,")
    print("navigation and a starter catalogue.\n")
    print("Next:")
    print("  1. Edit store.toml -- the brand, and every {{PLACEHOLDER}}.")
    print("  2. shop preview --open        see it before it exists")
    print("  3. shop doctor               once you have a token")
    print("  4. shop build --live         create the structure")
    print("  5. shop apply --live         create the products")
    return 0


def cmd_build(args) -> int:
    from .store import StoreSpec
    from . import storefront

    config = _config(args, live=args.live)
    try:
        spec = StoreSpec.load(args.store)
    except ValueError as exc:
        print(f"{exc}\n\nRun `shop scaffold` to create one.")
        return 1

    issues = spec.problems()
    if issues:
        print("The store definition still needs attention:")
        for issue in issues:
            print(f"  - {issue}")
        print()
    if config.problems():
        print("Cannot build yet:")
        for problem in config.problems():
            print(f"  - {problem}")
        return 1

    client = ShopifyClient(config.endpoint, config.token, dry_run=not args.live)
    try:
        build = storefront.plan(spec, client)
    except ShopifyError as exc:
        print(f"problem: {exc}")
        return 1

    for step in build.steps:
        print(f"  {step.describe()}")
    print(f"\n{build.summary()}")

    if not args.live:
        print("\nDry run. Add --live to build it.")
        return 0
    done = storefront.apply(build, client)
    print(f"\nbuilt: {done}")
    return 1 if done["failed"] else 0


def cmd_preview(args) -> int:
    from .store import StoreSpec
    from . import preview as preview_module

    config = _config(args)
    try:
        spec = StoreSpec.load(args.store)
    except ValueError as exc:
        print(f"{exc}\n\nRun `shop scaffold` to create one.")
        return 1
    index = preview_module.render(spec, config, args.out)
    print(f"Wrote a preview of the whole store to {index}")
    if args.open:
        import webbrowser

        webbrowser.open(f"file://{index.resolve()}")
    return 0


def cmd_doctor(args) -> int:
    config = _config(args)
    print(f"shop {__version__}\n")
    ok = True

    print("Configuration")
    problems = config.problems()
    for problem in problems:
        print(f"  \N{CROSS MARK} {problem}")
    if not problems:
        print(f"  \N{HEAVY CHECK MARK} {config.store_domain} (API {config.api_version})")
    ok &= not problems

    print("\nFeed")
    if config.feed_path:
        try:
            report = feed.read(config.feed_path)
            print(f"  \N{HEAVY CHECK MARK} {report.summary()}")
            print(f"      columns: {report.columns}")
            for row, why in report.rejected[:5]:
                print(f"      row {row}: {why}")
        except (ValueError, OSError) as exc:
            print(f"  \N{CROSS MARK} {exc}")
            ok = False
    else:
        print("  \N{CROSS MARK} no feed configured")
        ok = False

    print("\nStore")
    if problems:
        print("  - skipped, fix the configuration first")
    else:
        client = ShopifyClient(config.endpoint, config.token, dry_run=True)
        try:
            shop_info = client.check()
            print(
                f"  \N{HEAVY CHECK MARK} {shop_info.get('name')} "
                f"({shop_info.get('currencyCode')}, {shop_info.get('ianaTimezone')})"
            )
            location = catalog.first_location(client, config.location)
            print(
                f"  \N{HEAVY CHECK MARK} inventory location {location}"
                if location
                else "  \N{CROSS MARK} no active inventory location"
            )
            ok &= bool(location)
        except ShopifyError as exc:
            print(f"  \N{CROSS MARK} {exc}")
            ok = False

    print("\nCopywriter")
    writer = autopilot.make_writer(config)
    print(
        f"  \N{HEAVY CHECK MARK} {writer.backend.description}"
        if writer
        else "  - off; listings will use the supplier's own text"
    )

    print("\nReady." if ok else "\nNot ready -- fix the items above.")
    return 0 if ok else 1


def _print_plan(plan: catalog.Plan, limit: int = 40) -> None:
    if plan.feed and plan.feed.rejected:
        print(f"{len(plan.feed.rejected)} feed rows rejected:")
        for row, why in plan.feed.rejected[:5]:
            print(f"  row {row}: {why}")
        print()
    for action in plan.writes[:limit]:
        print(f"  {action.describe()}")
    if len(plan.writes) > limit:
        print(f"  ... and {len(plan.writes) - limit} more")
    held = plan.of(catalog.HOLD)
    if held:
        print(f"\n{len(held)} held for review:")
        for action in held[:10]:
            print(f"  {action.sku:<16} {action.reason}")
    print(f"\n{plan.summary()}")


def cmd_plan(args) -> int:
    config = _config(args, live=False)
    if config.problems():
        print("Cannot plan yet:")
        for problem in config.problems():
            print(f"  - {problem}")
        return 1
    result = autopilot.once(config, catalogue=True, process_orders=False)
    if result.errors:
        for error in result.errors:
            print(f"problem: {error}")
        return 1
    if result.plan:
        _print_plan(result.plan)
    return 0


def cmd_apply(args) -> int:
    config = _config(args, live=args.live)
    if config.problems():
        print("Cannot apply yet:")
        for problem in config.problems():
            print(f"  - {problem}")
        return 1
    if not args.live:
        print("Dry run. Nothing will be written. Add --live to apply.\n")
    result = autopilot.once(config, catalogue=True, process_orders=False)
    if result.plan:
        _print_plan(result.plan)
    if result.applied:
        print(f"\napplied: {result.applied}")
    for error in result.errors:
        print(f"problem: {error}")
    return 1 if result.errors else 0


def cmd_orders(args) -> int:
    config = _config(args, live=args.live)
    if config.problems():
        print("Cannot check orders yet:")
        for problem in config.problems():
            print(f"  - {problem}")
        return 1
    client = ShopifyClient(config.endpoint, config.token, dry_run=not args.live)
    ledger = Ledger.load(config.ledger_path)
    config._known_skus = ledger.live_skus or None
    try:
        recent = orders.fetch_orders(client, since=args.since)
    except ShopifyError as exc:
        print(f"problem: {exc}")
        return 1
    if not recent:
        print("No new orders.")
        return 0
    for result in orders.process(recent, client, config):
        print(f"  {result.describe()}")
    if not args.live:
        print("\nDry run -- no tags were written. Add --live to apply.")
    return 0


def cmd_status(args) -> int:
    config = _config(args)
    ledger = Ledger.load(config.ledger_path)
    if not ledger.entries:
        print(f"Nothing recorded yet ({config.ledger_path}).")
        return 0
    live = ledger.live_skus
    print(f"{len(live)} products managed, {len(ledger.entries) - len(live)} retired")
    low = orders.low_stock(ledger, config.low_stock_threshold)
    if low:
        print(f"\n{len(low)} low on stock:")
        for sku, quantity in low[:15]:
            print(f"  {sku:<16} {quantity}")
    print(f"\n({config.ledger_path})")
    return 0


def cmd_run(args) -> int:
    config = _config(args, live=args.live)
    if config.problems():
        print("Cannot run yet:")
        for problem in config.problems():
            print(f"  - {problem}")
        return 1
    if not args.live:
        print("Dry run mode: passes will report but write nothing.\n")
    try:
        return autopilot.run_forever(config)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        None: cmd_doctor,
        "init": cmd_init,
        "scaffold": cmd_scaffold,
        "build": cmd_build,
        "preview": cmd_preview,
        "doctor": cmd_doctor,
        "plan": cmd_plan,
        "apply": cmd_apply,
        "orders": cmd_orders,
        "status": cmd_status,
        "run": cmd_run,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())

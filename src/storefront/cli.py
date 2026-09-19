"""storefront -- run and watch a digital-products Shopify store.

    storefront doctor            check the token, the store and the files
    storefront audit             is every live product actually deliverable?
    storefront attach --live     attach digital files through your own browser
    storefront assets            what the agent thinks is attached
    storefront orders            recent orders, and anything odd about them
    storefront report            what the shop did
    storefront run --live        keep checking, on an interval
    storefront watch --live      24/7: check, alert, repeat
    storefront storefront-check  walk the live shop in a browser
    storefront serve --live      a 3D workspace showing what it is doing
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from shop.client import ShopifyClient, ShopifyError

from . import agent, alerts as alerts_mod, attach as attach_mod
from . import audit as audit_mod, orders as orders_mod, server as server_mod
from . import synthetic, watch as watch_mod
from . import queries, report as report_mod
from .assets import Asset, AssetLedger
from .config import Config

TICK = "\N{HEAVY CHECK MARK}"
CROSS = "\N{MULTIPLICATION SIGN}"


def _config(args) -> Config:
    config = Config.load(Path(args.config) if args.config else None)
    if getattr(args, "live", False):
        config.dry_run = False
    if getattr(args, "interval", None):
        config.interval_minutes = args.interval
    return config


def cmd_doctor(args) -> int:
    config = _config(args)
    print(f"config    {Path(args.config) if args.config else 'shop.toml'}")
    problems = config.validate()
    if problems:
        for p in problems:
            print(f"  {CROSS} {p}")
        return 1
    print(f"store     {config.store_domain}")
    client = ShopifyClient(config.endpoint, config.token, dry_run=True)
    try:
        shop = client.execute(queries.SHOP)["shop"]
    except ShopifyError as exc:
        print(f"  {CROSS} {exc}")
        return 1
    print(f"  {TICK} {shop.get('name')} ({shop.get('currencyCode')}, {shop.get('ianaTimezone')})")

    ledger = AssetLedger.load(config.ledger_path)
    print(f"assets    {len(ledger.assets)} recorded  ({config.ledger_path})")
    mapping = config.resolve_assets()
    if mapping:
        missing = [s for s, p in mapping.items() if not p.exists()]
        print(f"  {TICK if not missing else CROSS} {len(mapping)} mapped in config"
              + (f", {len(missing)} file(s) not on disk" if missing else ""))
        for sku in missing[:5]:
            print(f"      missing: {sku} -> {mapping[sku]}")
    else:
        print("  ! no [storefront.assets] mapping -- `attach` has nothing to do")
    return 0


def cmd_audit(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    client = ShopifyClient(config.endpoint, config.token, dry_run=True)
    ledger = AssetLedger.load(config.ledger_path)
    try:
        products = audit_mod.fetch_catalog(client)
    except ShopifyError as exc:
        print(f"problem: {exc}")
        return 1
    report = audit_mod.audit(products, assets_known=ledger.skus)

    print(f"{report.products_checked} product(s) checked\n")
    if not report.findings:
        print(f"{TICK} nothing to fix")
        return 0
    for finding in report.sorted():
        print(finding.describe())
    blockers = len(report.blockers)
    print()
    if blockers:
        print(f"{CROSS} {blockers} blocker(s): a customer could pay and get nothing")
        print("  fix these before you send anyone to the store")
    else:
        print(f"{TICK} no blockers -- the store is safe to sell from")
    return 1 if blockers else 0


def cmd_attach(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    mapping = config.resolve_assets()
    if not mapping:
        print("nothing mapped. Add a [storefront.assets] table to shop.toml:")
        print('  [storefront]\n  assets_dir = "~/solostack/deliverables"\n')
        print('  [storefront.assets]\n  "SS-RATE-CALC" = "Rate Calculator and Pricing Toolkit.xlsx"')
        return 1

    client = ShopifyClient(config.endpoint, config.token, dry_run=True)
    try:
        products = audit_mod.fetch_catalog(client)
    except ShopifyError as exc:
        print(f"problem: {exc}")
        return 1

    plans, unmatched = attach_mod.plan_from_mapping(mapping, products)
    for problem in unmatched:
        print(f"{CROSS} {problem}")
    if not plans:
        return 1

    ledger = AssetLedger.load(config.ledger_path)
    todo = [p for p in plans if args.force or p.sku not in ledger.skus]
    skipped = len(plans) - len(todo)

    print(f"{len(todo)} to attach" + (f", {skipped} already recorded" if skipped else ""))
    for plan in todo:
        print(f"  {plan.describe()}")
    if not todo:
        return 0

    if config.dry_run:
        print("\ndry run -- nothing was attached. Re-run with --live to do it.")
        print("A browser window will open; sign in to Shopify yourself if asked.")
        return 0

    print("\nopening a browser. Sign in if prompted -- the agent never sees your password.")
    attached, failed = attach_mod.attach_all(
        todo, config.store_domain,
        headless=args.headless, dry_run=False,
        on_done=lambda p: print(f"  {TICK} {p.sku}"),
    )
    for plan in attached:
        ledger.remember(Asset.from_file(plan.sku, plan.file))
    ledger.save()

    for plan, why in failed:
        print(f"  {CROSS} {plan.sku}: {why}")
    print(f"\n{len(attached)} attached, {len(failed)} left to do by hand")
    if failed:
        print("For the ones that failed, attach them in the admin and then run:")
        for plan, _ in failed:
            print(f"  storefront assets add {plan.sku} '{plan.file}'")
    return 0 if not failed else 1


def cmd_assets(args) -> int:
    config = _config(args)
    ledger = AssetLedger.load(config.ledger_path)
    if args.action == "add":
        path = Path(args.file).expanduser()
        if not path.exists():
            print(f"{CROSS} no such file: {path}")
            return 1
        ledger.remember(Asset.from_file(args.sku, path))
        ledger.save()
        print(f"{TICK} recorded {args.sku} -> {path.name}")
        return 0
    if args.action == "remove":
        ok = ledger.forget(args.sku)
        ledger.save()
        print(f"{TICK} forgot {args.sku}" if ok else f"{CROSS} {args.sku} was not recorded")
        return 0 if ok else 1

    if not ledger.assets:
        print("nothing recorded yet")
        return 0
    for sku, asset in sorted(ledger.assets.items()):
        mark = " (file on disk has changed)" if asset.source_path and asset.stale_against(
            Path(asset.source_path)) else ""
        print(f"  {sku:24} {asset.filename[:44]:46} {asset.bytes/1024:>7.0f}K{mark}")
    return 0


def cmd_orders(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    client = ShopifyClient(config.endpoint, config.token, dry_run=config.dry_run)
    ledger = AssetLedger.load(config.ledger_path)
    try:
        nodes = orders_mod.fetch(client, since=args.since or "")
    except ShopifyError as exc:
        print(f"problem: {exc}")
        return 1
    views = orders_mod.review(nodes, known_skus=ledger.skus,
                              review_above=config.review_above)
    if not views:
        print("no orders in that window")
        return 0
    for view in views:
        print(view.describe())
    flagged = sum(1 for v in views if v.needs_review)
    print(f"\n{len(views)} order(s), {flagged} needing review")
    if args.tag and not config.dry_run:
        n = orders_mod.tag_seen(client, [v for v in views if not v.seen])
        print(f"tagged {n}")
    elif args.tag:
        print("dry run -- no tags written. Use --live to tag.")
    return 0


def cmd_report(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    result = agent.run_once(config, since_days=args.days, tag=False)
    for err in result.errors:
        print(f"{CROSS} {err}")
    if result.errors:
        return 1
    print(report_mod.render(result.summary, period=f"Last {args.days} day(s)"))
    if result.blockers:
        print(f"\n{CROSS} {len(result.blockers)} product(s) cannot be delivered "
              "-- run `storefront audit`")
    return 0


def cmd_run(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    print(f"watching {config.store_domain} every {config.interval_minutes}m"
          + ("" if not config.dry_run else "  (dry run -- nothing will be written)"))

    def on_pass(result: agent.PassResult) -> None:
        print(result.headline())
        for finding in result.blockers:
            print(f"   {finding.describe()}")

    try:
        agent.run_forever(config, since_days=args.days, on_pass=on_pass)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_storefront_check(args) -> int:
    config = _config(args)
    if not config.store_domain:
        print(f"{CROSS} no store_domain in shop.toml")
        return 1
    handles = args.product or []
    if not handles:
        client = ShopifyClient(config.endpoint, config.token, dry_run=True)
        try:
            products = audit_mod.fetch_catalog(client)
        except ShopifyError as exc:
            print(f"problem: {exc}")
            return 1
        handles = [p["handle"] for p in products
                   if p.get("status") == "ACTIVE" and p.get("publishedAt")][:3]
    if not handles:
        print("nothing published to check")
        return 1

    print(f"walking {config.store_domain} in a browser...")
    report = synthetic.check_storefront(
        config.store_domain, product_handles=handles,
        collection_handles=args.collection or [], headless=not args.show,
        try_add_to_cart=not args.no_cart,
    )
    for check in report.checks:
        print(f"  {check.line()}")
    print()
    if report.ok:
        print(f"{TICK} the shop is standing up")
        return 0
    print(f"{CROSS} {len(report.failures)} check(s) failed")
    return 1


def _dispatcher(config, args) -> alerts_mod.Dispatcher:
    return alerts_mod.Dispatcher(
        state=alerts_mod.AlertState.load(config.alert_state_path),
        cooldown_minutes=config.alert_cooldown_minutes,
        console=True,
        desktop=config.alert_desktop or args.desktop,
        webhook_url=args.webhook or config.alert_webhook,
        email=config.alert_email,
        log_path=config.alert_log_path,
    )


def cmd_watch(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1

    dispatcher = _dispatcher(config, args)
    channels = ["console"]
    if dispatcher.desktop:
        channels.append("desktop")
    if dispatcher.webhook_url:
        channels.append("webhook")
    if dispatcher.email.get("to"):
        channels.append("email")
    channels.append(f"log ({config.alert_log_path})")

    print(f"watching {config.store_domain}")
    print(f"  every          {config.interval_minutes}m")
    print(f"  browser check  every {config.storefront_check_minutes}m"
          + ("" if config.browser_checks and not args.no_browser else "  (off)"))
    print(f"  alerts to      {', '.join(channels)}")
    print(f"  repeat after   {config.alert_cooldown_minutes}m")
    if config.dry_run:
        print("  dry run -- orders will not be tagged. Use --live to tag.")
    print("  Ctrl-C to stop\n")

    def on_pass(state, found):
        stamp = datetime.now().strftime("%H:%M:%S")
        if not found:
            print(f"{stamp}  pass {state.passes}: all clear")

    try:
        watch_mod.run(
            config, dispatcher,
            since_days=args.days,
            storefront_every_minutes=config.storefront_check_minutes,
            collection_handles=args.collection or [],
            browser_checks=config.browser_checks and not args.no_browser,
            on_pass=on_pass,
        )
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_serve(args) -> int:
    config = _config(args)
    if problems := config.validate():
        for p in problems:
            print(f"{CROSS} {p}")
        return 1
    dispatcher = _dispatcher(config, args)
    if config.dry_run:
        print("dry run -- orders will not be tagged. Use --live to tag.")
    return server_mod.serve(
        config, dispatcher,
        port=args.port, since_days=args.days,
        browser_checks=config.browser_checks and not args.no_browser,
        open_browser=not args.no_open,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storefront", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to shop.toml")
    p.add_argument("--debug", action="store_true")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("doctor", help="check the token, store and files")
    sub.add_parser("audit", help="is every live product deliverable?")

    a = sub.add_parser("attach", help="attach digital files via your own browser")
    a.add_argument("--live", action="store_true", help="actually open a browser and do it")
    a.add_argument("--headless", action="store_true", help="no visible window (sign in first)")
    a.add_argument("--force", action="store_true", help="re-attach even if already recorded")

    s = sub.add_parser("assets", help="what the agent thinks is attached")
    s.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    s.add_argument("sku", nargs="?")
    s.add_argument("file", nargs="?")

    o = sub.add_parser("orders", help="recent orders and anything odd")
    o.add_argument("--since", help="ISO date, e.g. 2026-09-01")
    o.add_argument("--tag", action="store_true", help="mark them handled")
    o.add_argument("--live", action="store_true")

    r = sub.add_parser("report", help="what the shop did")
    r.add_argument("--days", type=int, default=7)

    w = sub.add_parser("watch", help="24/7: check, alert, repeat")
    w.add_argument("--live", action="store_true", help="allow writes (order tagging)")
    w.add_argument("--days", type=int, default=7)
    w.add_argument("--interval", type=int, help="minutes between passes")
    w.add_argument("--desktop", action="store_true", help="desktop notifications")
    w.add_argument("--webhook", help="Slack/Discord webhook URL")
    w.add_argument("--collection", action="append", help="collection handle to check")
    w.add_argument("--no-browser", action="store_true", help="skip browser checks")

    sv = sub.add_parser("serve", help="a 3D workspace showing what it is doing")
    sv.add_argument("--live", action="store_true", help="allow writes (order tagging)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--days", type=int, default=7)
    sv.add_argument("--interval", type=int, help="minutes between passes")
    sv.add_argument("--desktop", action="store_true", help="desktop notifications too")
    sv.add_argument("--webhook", help="Slack/Discord webhook URL")
    sv.add_argument("--no-browser", action="store_true", help="skip storefront checks")
    sv.add_argument("--no-open", action="store_true", help="do not open a browser tab")

    sc = sub.add_parser("storefront-check", help="walk the live shop in a browser")
    sc.add_argument("--product", action="append", help="product handle")
    sc.add_argument("--collection", action="append", help="collection handle")
    sc.add_argument("--show", action="store_true", help="show the browser window")
    sc.add_argument("--no-cart", action="store_true", help="skip add-to-cart")

    run = sub.add_parser("run", help="keep checking")
    run.add_argument("--live", action="store_true")
    run.add_argument("--days", type=int, default=7)
    run.add_argument("--interval", type=int, help="minutes between passes")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    handlers = {
        None: cmd_doctor, "doctor": cmd_doctor, "audit": cmd_audit,
        "attach": cmd_attach, "assets": cmd_assets, "orders": cmd_orders,
        "report": cmd_report, "run": cmd_run, "watch": cmd_watch,
        "storefront-check": cmd_storefront_check, "serve": cmd_serve,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

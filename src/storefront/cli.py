"""storefront -- run and watch a digital-products Shopify store.

    storefront doctor            check the token, the store and the files
    storefront audit             is every live product actually deliverable?
    storefront attach --live     attach digital files through your own browser
    storefront assets            what the agent thinks is attached
    storefront orders            recent orders, and anything odd about them
    storefront report            what the shop did
    storefront run --live        keep checking, on an interval
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from shop.client import ShopifyClient, ShopifyError

from . import agent, attach as attach_mod, audit as audit_mod, orders as orders_mod
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
        "report": cmd_report, "run": cmd_run,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

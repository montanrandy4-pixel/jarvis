"""One pass of the storefront agent, and the loop around it.

The pass is deliberately ordered by how much a problem costs the merchant:

1. **Audit the catalogue.** A product that is live without a file is the
   worst state this store can be in, because it takes money and returns
   nothing. Everything else can wait behind that.
2. **Triage orders.** Anything odd gets tagged, once.
3. **Report.** What sold, what it made.

Nothing here fulfils, refunds, emails a customer or changes a price. Those
are all irreversible or outward-facing, and an agent that does them
unattended is a liability rather than an assistant. It looks, it tags, it
tells you.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shop.client import ShopifyClient, ShopifyError

from . import audit, orders as orders_mod, report as report_mod
from .assets import AssetLedger
from .config import Config

log = logging.getLogger("storefront.agent")


@dataclass
class PassResult:
    audit_report: audit.AuditReport | None = None
    order_views: list = field(default_factory=list)
    summary: report_mod.Summary | None = None
    tagged: int = 0
    errors: list[str] = field(default_factory=list)
    # Handles of live products, so a browser check knows which pages to walk.
    live_handles: list[str] = field(default_factory=list)
    # Title, sku, price and handle per product, for anything drawing the
    # catalogue rather than just judging it.
    catalog: list[dict] = field(default_factory=list)

    @property
    def blockers(self) -> list:
        return self.audit_report.blockers if self.audit_report else []

    def headline(self) -> str:
        """The one line worth pushing to a phone."""
        if self.errors:
            return f"storefront: {self.errors[0][:90]}"
        blocked = len(self.blockers)
        if blocked:
            return f"storefront: {blocked} product(s) cannot be delivered to a buyer"
        if self.summary and self.summary.needs_review:
            return f"storefront: {self.summary.needs_review} order(s) need review"
        if self.summary and self.summary.orders:
            return (
                f"storefront: {self.summary.orders} order(s), "
                f"{self.summary.revenue:,.2f} {self.summary.currency}"
            )
        return "storefront: all clear"


def run_once(
    config: Config,
    *,
    since_days: int = 7,
    tag: bool = True,
) -> PassResult:
    result = PassResult()
    problems = config.validate()
    if problems:
        result.errors.extend(problems)
        return result

    client = ShopifyClient(config.endpoint, config.token, dry_run=config.dry_run)
    ledger = AssetLedger.load(config.ledger_path)

    try:
        products = audit.fetch_catalog(client)
        result.audit_report = audit.audit(products, assets_known=ledger.skus)
        result.live_handles = [
            h for p in products
            if p.get("status") == "ACTIVE" and p.get("publishedAt")
            and (h := p.get("handle"))
        ]
        result.catalog = [_describe(p) for p in products]
    except ShopifyError as exc:
        result.errors.append(f"could not read the catalogue: {exc}")
        return result

    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
    try:
        nodes = orders_mod.fetch(client, since=since)
        result.order_views = orders_mod.review(
            nodes, known_skus=ledger.skus, review_above=config.review_above
        )
        result.summary = report_mod.summarise(nodes, result.order_views)
    except ShopifyError as exc:
        result.errors.append(f"could not read orders: {exc}")
        return result

    if tag and not config.dry_run:
        unseen = [v for v in result.order_views if not v.seen]
        try:
            result.tagged = orders_mod.tag_seen(client, unseen)
        except ShopifyError as exc:
            result.errors.append(f"could not tag orders: {exc}")

    # A file that changed on disk after it was uploaded is a silent problem:
    # customers keep receiving the old version. Surface it with the rest.
    for stale in ledger.stale():
        result.audit_report.findings.append(audit.Finding(
            audit.WARNING,
            stale.sku,
            "the file on disk has changed since it was uploaded",
            "re-attach it so buyers get the current version",
        ))

    return result


def _describe(product: dict) -> dict:
    """The few fields a view needs, unescaped and flattened."""
    import html

    variants = (product.get("variants") or {}).get("nodes") or []
    first = variants[0] if variants else {}
    try:
        price = float(first.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return {
        "title": html.unescape(str(product.get("title") or "")).strip(),
        "sku": first.get("sku") or "",
        "price": price,
        "handle": product.get("handle") or "",
        "status": product.get("status") or "",
    }


def run_forever(config: Config, *, since_days: int = 7, on_pass=None) -> None:
    """Keep running. Survives transient failures; stops on Ctrl-C."""
    interval = max(60, config.interval_minutes * 60)
    while True:
        started = time.monotonic()
        try:
            result = run_once(config, since_days=since_days)
            if on_pass:
                on_pass(result)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 -- a loop that dies is useless
            log.exception("pass failed: %s", exc)
        elapsed = time.monotonic() - started
        try:
            time.sleep(max(5.0, interval - elapsed))
        except KeyboardInterrupt:
            return

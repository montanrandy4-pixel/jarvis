"""One pass of the autopilot, and the loop that repeats it."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import catalog, feed, orders
from .client import ShopifyClient
from .ledger import Ledger

log = logging.getLogger("shop.autopilot")


@dataclass
class Result:
    plan: catalog.Plan | None = None
    applied: dict = field(default_factory=dict)
    triaged: list = field(default_factory=list)
    low_stock: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = True

    def summary(self) -> str:
        lines = []
        if self.plan:
            lines.append(f"catalogue: {self.plan.summary()}")
        if self.applied:
            done = ", ".join(
                f"{count} {name}" for name, count in self.applied.items() if count
            )
            lines.append(f"applied: {done or 'nothing'}")
        if self.triaged:
            flagged = sum(1 for t in self.triaged if t.needs_review)
            lines.append(
                f"orders: {len(self.triaged)} new"
                + (f", {flagged} flagged for review" if flagged else "")
            )
        if self.low_stock:
            worst = ", ".join(f"{sku} ({qty})" for sku, qty in self.low_stock[:5])
            lines.append(f"low stock: {worst}")
        for error in self.errors:
            lines.append(f"problem: {error}")
        if self.dry_run:
            lines.append("dry run -- nothing was written to the store")
        return "\n".join(lines) or "nothing to do"


def make_writer(config):
    """The copywriter, if the local model is available and wanted."""
    if not config.ai_copy:
        return None
    try:
        from jarvis.backends import make_backend
        from jarvis.config import Config as JarvisConfig

        from .copy import Writer

        backend = make_backend(JarvisConfig.load())
        ready, detail = backend.health()
        if not ready:
            log.warning("no local model for product copy (%s)", detail)
            return None
        return Writer(backend, config.brand_voice)
    except Exception as exc:
        log.warning("copywriter unavailable: %s", exc)
        return None


def once(config, *, client: ShopifyClient | None = None, writer=None,
         catalogue: bool = True, process_orders: bool = True) -> Result:
    """Run a single pass: reconcile the catalogue, then triage orders."""
    result = Result(dry_run=config.dry_run)
    client = client or ShopifyClient(
        endpoint=config.endpoint, token=config.token, dry_run=config.dry_run
    )
    ledger = Ledger.load(config.ledger_path)

    if catalogue and config.feed_path:
        try:
            report = feed.read(config.feed_path)
        except (ValueError, OSError) as exc:
            result.errors.append(f"feed: {exc}")
            report = None
        if report is not None:
            store = catalog.fetch_store_products(client, config)
            plan = catalog.plan(report, store, ledger, config)
            result.plan = plan
            if not config.dry_run and plan.writes:
                location = catalog.first_location(client, config.location)
                if not location:
                    result.errors.append("no active inventory location found")
                result.applied = catalog.apply(
                    plan, client, ledger, config, location, writer
                )
            else:
                ledger.save()

    if process_orders and config.process_orders:
        config._known_skus = ledger.live_skus or None
        try:
            new_orders = orders.fetch_orders(client)
            result.triaged = orders.process(new_orders, client, config)
        except Exception as exc:
            result.errors.append(f"orders: {exc}")

    result.low_stock = orders.low_stock(ledger, config.low_stock_threshold)
    return result


def run_forever(config, *, on_result=None) -> int:
    """Run a pass every ``interval_minutes`` until interrupted."""
    writer = make_writer(config)
    interval = max(5, config.interval_minutes) * 60
    log.info("autopilot starting, every %d minutes", config.interval_minutes)
    while True:
        started = time.time()
        try:
            result = once(config, writer=writer)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.exception("pass failed")
            result = Result(dry_run=config.dry_run, errors=[str(exc)])
        if on_result:
            on_result(result)
        else:
            print(f"\n[{time.strftime('%H:%M:%S')}]\n{result.summary()}", flush=True)
        elapsed = time.time() - started
        try:
            time.sleep(max(1.0, interval - elapsed))
        except KeyboardInterrupt:
            return 0

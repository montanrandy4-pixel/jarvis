"""The 24/7 watch.

Three checks, on three different clocks, because they cost different amounts
and change at different speeds:

* **Catalogue** -- cheap, every pass. A product going live without a file is
  the most expensive thing that can silently happen here.
* **Orders** -- cheap, every pass. Something odd about a payment should not
  wait an hour to be noticed.
* **Storefront** -- expensive (it drives a browser), so much less often. It
  catches what the API cannot see: a theme update that broke the buy button.

Findings become alerts, alerts are deduplicated, and the loop survives
everything. A watch that dies at 3am because a webhook timed out is worse
than no watch, because you believe you are being watched.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from shop.client import ShopifyError

from . import agent, alerts as alerts_mod, audit as audit_mod, synthetic
from .alerts import CRITICAL, INFO, WARNING, Alert
from .config import Config

log = logging.getLogger("storefront.watch")


@dataclass
class WatchState:
    last_storefront_check: float = 0.0
    passes: int = 0
    consecutive_failures: int = 0
    started: float = field(default_factory=time.monotonic)


def catalogue_alerts(report: audit_mod.AuditReport) -> list[Alert]:
    out = []
    for finding in report.findings:
        severity = {
            audit_mod.BLOCKER: CRITICAL,
            audit_mod.WARNING: WARNING,
            audit_mod.NOTE: INFO,
        }[finding.severity]
        out.append(Alert(
            severity=severity,
            title=f"{finding.product}: {finding.problem}",
            detail=finding.fix,
            key=f"catalog:{finding.product}:{finding.problem[:40]}",
        ))
    return out


def order_alerts(views: list) -> list[Alert]:
    out = []
    for view in views:
        if not view.needs_review:
            continue
        out.append(Alert(
            severity=WARNING,
            title=f"order {view.name} needs review",
            detail="; ".join(view.concerns),
            key=f"order:{view.name}",
        ))
    return out


def storefront_alerts(report: synthetic.StorefrontReport) -> list[Alert]:
    if not report.reachable:
        detail = report.failures[0].detail if report.failures else "unknown"
        return [Alert(CRITICAL, "the storefront could not be reached", detail,
                      key="storefront:unreachable")]
    return [
        Alert(
            severity=CRITICAL,
            title=f"storefront: {check.name} failed",
            detail=check.detail,
            key=f"storefront:{check.name}",
        )
        for check in report.failures
    ]


def sales_alerts(summary) -> list[Alert]:
    """Good news, once per order, so a sale is something you feel."""
    if not summary or not summary.orders:
        return []
    return [Alert(
        severity=INFO,
        title=f"{summary.orders} order(s), {summary.revenue:,.2f} {summary.currency}",
        detail=f"average {summary.average_order:,.2f}",
        # Keyed by the window's totals: it re-fires only when they change.
        key=f"sales:{summary.orders}:{summary.revenue:.2f}",
    )]


def one_pass(
    config: Config,
    dispatcher: alerts_mod.Dispatcher,
    state: WatchState,
    *,
    since_days: int = 7,
    storefront_every_minutes: int = 60,
    product_handles: list[str] | None = None,
    collection_handles: list[str] | None = None,
    browser_checks: bool = True,
) -> list[Alert]:
    found: list[Alert] = []
    state.passes += 1

    try:
        result = agent.run_once(config, since_days=since_days, tag=not config.dry_run)
        state.consecutive_failures = 0
    except ShopifyError as exc:
        state.consecutive_failures += 1
        found.append(Alert(CRITICAL, "cannot reach the Shopify API", str(exc)[:160],
                           key="api:unreachable"))
        dispatcher.dispatch(found)
        return found

    for message in result.errors:
        found.append(Alert(CRITICAL, "storefront agent error", message,
                           key=f"agent:{message[:40]}"))
    if result.audit_report:
        found += catalogue_alerts(result.audit_report)
    found += order_alerts(result.order_views)
    found += sales_alerts(result.summary)

    # The browser check runs on its own, slower clock.
    now = time.monotonic()
    due = (now - state.last_storefront_check) >= storefront_every_minutes * 60
    if browser_checks and due and config.store_domain:
        state.last_storefront_check = now
        handles = product_handles or result.live_handles
        if handles:
            report = synthetic.check_storefront(
                config.store_domain,
                product_handles=handles,
                collection_handles=collection_handles or [],
                headless=True,
            )
            found += storefront_alerts(report)

    dispatcher.dispatch(found)
    return found


def run(
    config: Config,
    dispatcher: alerts_mod.Dispatcher,
    *,
    since_days: int = 7,
    storefront_every_minutes: int = 60,
    product_handles: list[str] | None = None,
    collection_handles: list[str] | None = None,
    browser_checks: bool = True,
    on_pass=None,
) -> None:
    """Watch until interrupted. Nothing short of Ctrl-C stops this."""
    state = WatchState()
    interval = max(60, config.interval_minutes * 60)
    log.info("watching %s every %ss", config.store_domain, interval)

    while True:
        started = time.monotonic()
        try:
            found = one_pass(
                config, dispatcher, state,
                since_days=since_days,
                storefront_every_minutes=storefront_every_minutes,
                product_handles=product_handles,
                collection_handles=collection_handles,
                browser_checks=browser_checks,
            )
            if on_pass:
                on_pass(state, found)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 -- never let a pass kill the watch
            state.consecutive_failures += 1
            log.exception("pass failed: %s", exc)
            if state.consecutive_failures in (3, 12, 48):
                # Escalate slowly rather than every pass: something is
                # persistently wrong and you should hear about it once.
                try:
                    dispatcher.dispatch([Alert(
                        CRITICAL, "the shop watch keeps failing",
                        f"{state.consecutive_failures} passes in a row: {exc}"[:200],
                        key="watch:failing",
                    )])
                except Exception:  # noqa: BLE001
                    log.exception("could not even send the failure alert")

        elapsed = time.monotonic() - started
        try:
            time.sleep(max(5.0, interval - elapsed))
        except KeyboardInterrupt:
            return

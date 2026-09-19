"""Is this catalogue actually safe to sell?

A digital storefront fails differently from a physical one. There is no stock
to run out and nothing to lose in the post; the failure mode is quieter and
worse -- a buyer pays, and receives nothing, because the listing was live
before the file was attached. Nothing in Shopify prevents that. This module
exists to catch it.

Every check answers a question a customer would ask with their money:

* Can I find it?        -- published to the Online Store
* Does it look real?    -- has a cover image that actually processed
* Will checkout work?   -- no shipping address demanded for a download
* Will I get the file?  -- an asset is attached
* Is the price sane?    -- not zero, not unset

Findings carry a severity. ``BLOCKER`` means a buyer can pay today and be
disappointed; those are the ones worth waking up for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shop.client import ShopifyClient

from . import queries

BLOCKER = "blocker"
WARNING = "warning"
NOTE = "note"

SEVERITY_ORDER = {BLOCKER: 0, WARNING: 1, NOTE: 2}


@dataclass
class Finding:
    """One thing wrong with one product."""

    severity: str
    product: str
    problem: str
    fix: str

    def describe(self) -> str:
        mark = {BLOCKER: "!!", WARNING: " !", NOTE: "  "}[self.severity]
        return f"{mark} {self.product[:46]:48} {self.problem}"


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    products_checked: int = 0
    # SKUs the agent has been told carry a digital asset. Shopify's Digital
    # Products app keeps its attachments in its own store, outside the Admin
    # API, so the agent cannot see them directly -- it is told, and remembers.
    assets_known: set[str] = field(default_factory=set)

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCKER]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def sorted(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.product))


def fetch_catalog(client: ShopifyClient, query: str = "") -> list[dict]:
    """Every product the store would show a customer, plus draft ones."""
    return list(
        client.paginate(queries.CATALOG, {"query": query}, path=["products"])
    )


def audit(
    products: list[dict],
    *,
    assets_known: set[str] | None = None,
    expect_digital: bool = True,
) -> AuditReport:
    """Judge a catalogue. Pure: give it products, get findings back.

    ``assets_known`` is the set of SKUs recorded as having a file attached.
    Pass an empty set on a store where nothing has been attached yet and every
    active product will report a blocker -- which is the correct answer.
    """
    known = assets_known or set()
    report = AuditReport(assets_known=set(known))

    for product in products:
        title = _text(product.get("title"))
        status = product.get("status")
        report.products_checked += 1
        variants = (product.get("variants") or {}).get("nodes") or []
        skus = [v.get("sku") or "" for v in variants]

        if status != "ACTIVE":
            report.findings.append(Finding(
                NOTE, title, f"status is {status}, so customers cannot buy it",
                "set it to Active once its file is attached",
            ))
            # A draft product cannot disappoint anyone. Skip the rest.
            continue

        if not product.get("publishedAt"):
            report.findings.append(Finding(
                BLOCKER, title, "active but not published to the Online Store",
                "publish it to the Online Store channel",
            ))

        if expect_digital:
            # A blank SKU is not "no problem", it is a worse one: nothing can
            # be matched to it, so no file can ever be confirmed attached.
            usable = [s for s in skus if s]
            if not usable:
                report.findings.append(Finding(
                    BLOCKER, title,
                    "no SKU, so no file can be matched to it -- a buyer would "
                    "pay and receive nothing",
                    "give the variant a SKU, then attach its file",
                ))
            elif missing := [s for s in usable if s not in known]:
                report.findings.append(Finding(
                    BLOCKER, title,
                    f"no digital file recorded for {', '.join(sorted(missing))} "
                    "-- a buyer would pay and receive nothing",
                    "attach the file, then run `storefront assets add <sku> <file>`",
                ))

        for variant in variants:
            item = variant.get("inventoryItem") or {}
            if item.get("requiresShipping"):
                report.findings.append(Finding(
                    BLOCKER, title,
                    "checkout will demand a shipping address for a download",
                    "set requiresShipping to false on the variant",
                ))
            price = variant.get("price")
            if price in (None, "", "0.00", "0"):
                report.findings.append(Finding(
                    BLOCKER, title, f"price is {price!r}",
                    "set a price before selling it",
                ))

        if not product.get("featuredMedia"):
            report.findings.append(Finding(
                WARNING, title, "no cover image",
                "add one -- an imageless product converts badly",
            ))

        for media in (product.get("media") or {}).get("nodes") or []:
            errors = media.get("mediaErrors") or []
            if errors:
                detail = errors[0].get("message", "media error")
                report.findings.append(Finding(
                    WARNING, title, f"an image failed to process: {detail}",
                    "re-upload the image",
                ))
            elif media.get("status") not in (None, "READY"):
                report.findings.append(Finding(
                    NOTE, title, f"image status is {media.get('status')}",
                    "wait for it to finish processing, then re-check",
                ))

        if not product.get("tags"):
            report.findings.append(Finding(
                NOTE, title, "no tags, so smart collections will not pick it up",
                "tag it to file it automatically",
            ))

    return report


def _text(value: object) -> str:
    """Shopify returns titles HTML-escaped; make them readable again."""
    import html

    return html.unescape(str(value or "")).strip()

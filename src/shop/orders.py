"""Watching orders as they come in.

What happens here is reversible: orders are read, classified and tagged, and
anything unusual is flagged for a person. Marking an order fulfilled is not
reversible and is not done automatically -- see `auto_fulfill` in the README.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from . import queries
from .client import ShopifyClient, UserError

log = logging.getLogger("shop.orders")

REVIEW_TAG = "needs-review"
SEEN_TAG = "autopilot-seen"


@dataclass
class Triage:
    order_id: str
    name: str
    total: Decimal
    currency: str = ""
    tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    skus: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return REVIEW_TAG in self.tags

    def describe(self) -> str:
        head = f"{self.name:<10} {self.total:>9.2f} {self.currency}"
        if self.reasons:
            return f"{head}  review: {'; '.join(self.reasons)}"
        return f"{head}  ok"


def fetch_orders(client: ShopifyClient, *, since: str = "", limit: int = 50,
                 unseen_only: bool = True) -> list[dict]:
    """Recent orders, newest last. ``since`` is an ISO date or Shopify query."""
    terms = []
    if since:
        terms.append(f"created_at:>='{since}'")
    if unseen_only:
        terms.append(f"-tag:{SEEN_TAG}")
    query = " AND ".join(terms)

    orders = []
    for order in client.paginate(
        queries.RECENT_ORDERS, {"query": query}, path=["orders"], page_size=25
    ):
        orders.append(order)
        if len(orders) >= limit:
            break
    return orders


def triage(order: dict, config) -> Triage:
    """Decide what to do with one order, without doing it yet."""
    money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
    try:
        total = Decimal(str(money.get("amount", "0")))
    except Exception:
        total = Decimal("0")

    skus = [
        (edge.get("node") or {}).get("sku") or ""
        for edge in ((order.get("lineItems") or {}).get("edges") or [])
    ]
    result = Triage(
        order_id=order.get("id", ""),
        name=order.get("name", ""),
        total=total,
        currency=money.get("currencyCode", ""),
        skus=[s for s in skus if s],
    )
    result.tags.append(SEEN_TAG)

    if config.review_above and total >= Decimal(str(config.review_above)):
        result.reasons.append(f"total {total} is at or above {config.review_above}")
    if order.get("displayFinancialStatus") not in {"PAID", "PARTIALLY_REFUNDED", None}:
        result.reasons.append(
            f"payment is {order.get('displayFinancialStatus', 'unknown').lower()}"
        )
    if not result.skus:
        result.reasons.append("no SKUs on the line items")
    unknown = [s for s in result.skus if s and not _ours(s, config)]
    if unknown and config.manage_only_own:
        result.reasons.append(f"contains items we do not manage: {', '.join(unknown[:3])}")

    if result.reasons:
        result.tags.append(REVIEW_TAG)
    return result


def _ours(sku: str, config) -> bool:
    known = getattr(config, "_known_skus", None)
    return True if known is None else sku in known


def process(orders: list[dict], client: ShopifyClient, config) -> list[Triage]:
    """Tag each order according to its triage. Returns what was decided."""
    decided = []
    for order in orders:
        result = triage(order, config)
        decided.append(result)
        if not result.order_id:
            continue
        try:
            client.mutate(
                queries.TAG_ORDER,
                {"id": result.order_id, "tags": sorted(set(result.tags))},
                field_name="tagsAdd",
            )
        except UserError as exc:
            log.error("could not tag %s: %s", result.name, exc)
    return decided


def low_stock(ledger, threshold: int) -> list[tuple[str, int]]:
    """SKUs at or below the threshold, worst first."""
    low = [
        (entry.sku, entry.quantity)
        for entry in ledger.entries.values()
        if not entry.retired and entry.quantity <= threshold
    ]
    return sorted(low, key=lambda pair: pair[1])

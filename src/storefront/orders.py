"""Watching orders on a store that sells files.

A digital order needs almost nothing done to it -- the app delivers the file
the moment payment clears. So this does not try to fulfil anything. It
watches for the handful of ways a digital order goes wrong, which are
different from the physical ones:

* payment authorised but never captured, so the file never went out
* a line item whose SKU the agent has no record of a file for
* an unusually large order, which on a digital store often means card testing
* a repeat buyer purchasing something they already own

Everything it sees once is tagged, so a later run does not report it twice.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from shop.client import ShopifyClient

from . import queries

SEEN_TAG = "storefront-seen"
REVIEW_TAG = "needs-review"

PAID = {"PAID", "PARTIALLY_REFUNDED"}


@dataclass
class OrderView:
    """One order, and what the agent makes of it."""

    id: str
    name: str
    created_at: str
    total: float
    currency: str
    financial_status: str
    email: str
    customer: str
    order_count: int
    tags: list[str]
    skus: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)

    @property
    def seen(self) -> bool:
        return SEEN_TAG in self.tags

    @property
    def needs_review(self) -> bool:
        return bool(self.concerns)

    def describe(self) -> str:
        money = f"{self.total:>9,.2f} {self.currency}"
        verdict = "review: " + "; ".join(self.concerns) if self.concerns else "ok"
        return f"{self.name:<10} {money}  {verdict}"


def fetch(client: ShopifyClient, *, since: str = "", limit: int = 100) -> list[dict]:
    query = f"created_at:>='{since}'" if since else ""
    out = []
    for node in client.paginate(queries.ORDERS, {"query": query}, path=["orders"]):
        out.append(node)
        if len(out) >= limit:
            break
    return out


def review(
    orders: list[dict],
    *,
    known_skus: set[str],
    review_above: float = 250.0,
) -> list[OrderView]:
    views = []
    for node in orders:
        money = (node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
        customer = node.get("customer") or {}
        items = (node.get("lineItems") or {}).get("nodes") or []
        skus = [i.get("sku") or "" for i in items]

        view = OrderView(
            id=str(node.get("id") or ""),
            name=str(node.get("name") or ""),
            created_at=str(node.get("createdAt") or ""),
            total=float(money.get("amount") or 0),
            currency=str(money.get("currencyCode") or ""),
            financial_status=str(node.get("displayFinancialStatus") or ""),
            email=str(node.get("email") or ""),
            customer=html.unescape(str(customer.get("displayName") or "")),
            order_count=int(customer.get("numberOfOrders") or 0),
            tags=list(node.get("tags") or []),
            skus=[s for s in skus if s],
        )

        if view.financial_status.upper() not in PAID:
            view.concerns.append(
                f"payment is {view.financial_status.lower() or 'unknown'} -- "
                "the file may not have been delivered"
            )

        unknown = [s for s in view.skus if s and s not in known_skus]
        if unknown:
            view.concerns.append(
                f"no file recorded for {', '.join(sorted(set(unknown)))}"
            )

        if any(i.get("requiresShipping") for i in items):
            view.concerns.append(
                "a line item still requires shipping -- checkout asked for an address"
            )

        if review_above and view.total >= review_above:
            view.concerns.append(f"total {view.total:,.2f} is at or above {review_above:,.0f}")

        views.append(view)
    return views


def tag_seen(client: ShopifyClient, views: list[OrderView]) -> int:
    """Mark orders handled, so the next run stays quiet about them."""
    tagged = 0
    for view in views:
        if view.seen:
            continue
        tags = [SEEN_TAG] + ([REVIEW_TAG] if view.needs_review else [])
        client.mutate(
            queries.ORDER_TAGS_ADD,
            {"id": view.id, "tags": tags},
            field_name="tagsAdd",
        )
        tagged += 1
    return tagged

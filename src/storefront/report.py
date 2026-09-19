"""What the shop did, in a form worth reading over coffee.

Deliberately small. A digital store's numbers are simple -- revenue, orders,
what sold -- and a report nobody reads is worse than no report.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Summary:
    orders: int = 0
    revenue: float = 0.0
    currency: str = "USD"
    units: int = 0
    by_product: Counter = field(default_factory=Counter)
    revenue_by_product: Counter = field(default_factory=Counter)
    needs_review: int = 0
    new_customers: int = 0

    @property
    def average_order(self) -> float:
        return self.revenue / self.orders if self.orders else 0.0


def summarise(order_nodes: list[dict], views: list) -> Summary:
    s = Summary()
    by_id = {v.id: v for v in views}

    for node in order_nodes:
        money = (node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
        total = float(money.get("amount") or 0)
        s.orders += 1
        s.revenue += total
        if code := money.get("currencyCode"):
            s.currency = code

        customer = node.get("customer") or {}
        if int(customer.get("numberOfOrders") or 0) <= 1:
            s.new_customers += 1

        view = by_id.get(str(node.get("id") or ""))
        if view and view.needs_review:
            s.needs_review += 1

        items = (node.get("lineItems") or {}).get("nodes") or []
        # Shopify does not break the order total down per line here, so
        # revenue is attributed by unit share -- good enough to rank sellers,
        # and not presented as accounting.
        units = sum(int(i.get("quantity") or 0) for i in items) or 1
        for item in items:
            import html

            title = html.unescape(str(item.get("title") or "untitled"))
            qty = int(item.get("quantity") or 0)
            s.units += qty
            s.by_product[title] += qty
            s.revenue_by_product[title] += total * (qty / units)
    return s


def render(s: Summary, *, period: str, width: int = 46) -> str:
    lines = [
        f"{period}",
        "",
        f"  revenue        {s.revenue:>12,.2f} {s.currency}",
        f"  orders         {s.orders:>12,}",
        f"  average order  {s.average_order:>12,.2f} {s.currency}",
        f"  units          {s.units:>12,}",
        f"  new customers  {s.new_customers:>12,}",
    ]
    if s.needs_review:
        lines.append(f"  needs review   {s.needs_review:>12,}   <-- look at these")
    if s.by_product:
        lines += ["", "  best sellers"]
        for title, qty in s.by_product.most_common(5):
            share = s.revenue_by_product[title]
            lines.append(f"    {title[:width]:<{width}} {qty:>3}  {share:>9,.2f}")
    if not s.orders:
        lines += ["", "  no orders in this period"]
    return "\n".join(lines)

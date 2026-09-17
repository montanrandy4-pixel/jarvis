"""Reconciling the supplier feed with what is actually in the store.

Two phases, always. :func:`plan` reads the feed and the store and works out
what would change; :func:`apply` carries it out. Nothing writes without a plan,
so `shop plan` is an honest preview of `shop apply`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from . import queries
from .client import ShopifyClient, UserError
from .feed import FeedItem, FeedReport
from .ledger import Entry, Ledger, content_hash
from .pricing import price_for, worth_changing

log = logging.getLogger("shop.catalog")

CREATE = "create"
REPRICE = "reprice"
RESTOCK = "restock"
RETIRE = "retire"
HOLD = "hold"


@dataclass
class Action:
    kind: str
    sku: str
    title: str = ""
    reason: str = ""
    # What the action would set, for display and for apply().
    price: str = ""
    compare_at: str | None = None
    quantity: int = 0
    product_id: str = ""
    variant_id: str = ""
    inventory_item_id: str = ""
    item: FeedItem | None = None

    def describe(self) -> str:
        head = f"{self.kind:<8} {self.sku:<16}"
        if self.kind == CREATE:
            return f"{head} {self.title[:44]:<44} {self.price} x{self.quantity}"
        if self.kind == REPRICE:
            return f"{head} {self.reason}"
        if self.kind == RESTOCK:
            return f"{head} stock -> {self.quantity}"
        return f"{head} {self.reason}"


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    unchanged: int = 0
    feed: FeedReport | None = None

    def of(self, kind: str) -> list[Action]:
        return [a for a in self.actions if a.kind == kind]

    @property
    def writes(self) -> list[Action]:
        """Actions that would change the store."""
        return [a for a in self.actions if a.kind != HOLD]

    def summary(self) -> str:
        counts = {
            kind: len(self.of(kind))
            for kind in (CREATE, REPRICE, RESTOCK, RETIRE, HOLD)
        }
        parts = [f"{count} {kind}" for kind, count in counts.items() if count]
        parts.append(f"{self.unchanged} unchanged")
        return ", ".join(parts)


def fetch_store_products(client: ShopifyClient, config) -> dict[str, dict]:
    """Index the products the autopilot manages by SKU."""
    query = f"tag:{config.order_tag}" if config.manage_only_own else ""
    by_sku: dict[str, dict] = {}
    for product in client.paginate(
        queries.MANAGED_PRODUCTS, {"query": query}, path=["products"]
    ):
        for edge in (product.get("variants") or {}).get("edges", []):
            variant = edge.get("node") or {}
            sku = (variant.get("sku") or "").strip()
            if sku:
                by_sku[sku] = {"product": product, "variant": variant}
    return by_sku


def plan(feed: FeedReport, store: dict[str, dict], ledger: Ledger, config) -> Plan:
    """Work out what the store should look like, against what it does."""
    result = Plan(feed=feed)
    rules = config.pricing
    seen: set[str] = set()

    for item in feed.items:
        seen.add(item.sku)
        priced = price_for(item.cost, rules, suggested=item.price)
        if priced.held:
            result.actions.append(
                Action(HOLD, item.sku, item.title, priced.hold, item=item)
            )
            continue

        amount, compare_at = priced.as_strings()
        existing = store.get(item.sku)
        entry = ledger.get(item.sku)

        if not existing:
            if entry and entry.product_id and not entry.retired:
                # The ledger says we made it but the store does not show it --
                # do not create a second copy on a bad read.
                result.actions.append(
                    Action(
                        HOLD,
                        item.sku,
                        item.title,
                        "ledger has a product id but the store did not return it",
                        item=item,
                    )
                )
                continue
            result.actions.append(
                Action(
                    CREATE,
                    item.sku,
                    item.title,
                    "not in the store",
                    price=amount,
                    compare_at=compare_at,
                    quantity=item.quantity,
                    item=item,
                )
            )
            continue

        variant = existing["variant"]
        product = existing["product"]
        changed = False

        if worth_changing(variant.get("price"), Decimal(amount), rules.min_change):
            result.actions.append(
                Action(
                    REPRICE,
                    item.sku,
                    item.title,
                    f"{variant.get('price')} -> {amount}",
                    price=amount,
                    compare_at=compare_at,
                    product_id=product.get("id", ""),
                    variant_id=variant.get("id", ""),
                    item=item,
                )
            )
            changed = True

        current_stock = variant.get("inventoryQuantity")
        if current_stock is not None and int(current_stock) != item.quantity:
            result.actions.append(
                Action(
                    RESTOCK,
                    item.sku,
                    item.title,
                    f"{current_stock} -> {item.quantity}",
                    quantity=item.quantity,
                    product_id=product.get("id", ""),
                    variant_id=variant.get("id", ""),
                    inventory_item_id=(variant.get("inventoryItem") or {}).get(
                        "id", ""
                    ),
                    item=item,
                )
            )
            changed = True

        if not changed:
            result.unchanged += 1

    # Anything the store sells that the feed no longer offers.
    for sku, existing in store.items():
        if sku in seen:
            continue
        product = existing["product"]
        if product.get("status") != "ACTIVE":
            continue  # Already retired.
        result.actions.append(
            Action(
                RETIRE,
                sku,
                product.get("title", ""),
                f"no longer in the feed ({config.on_missing})",
                product_id=product.get("id", ""),
            )
        )
    return result


def apply(plan_: Plan, client: ShopifyClient, ledger: Ledger, config,
          location_id: str = "", writer=None) -> dict:
    """Carry out a plan. Returns a count of what happened."""
    done = {"created": 0, "repriced": 0, "restocked": 0, "retired": 0,
            "failed": 0, "held": len(plan_.of(HOLD))}

    for action in plan_.writes:
        try:
            if action.kind == CREATE:
                _create(action, client, ledger, config, location_id, writer)
                done["created"] += 1
            elif action.kind == REPRICE:
                _reprice(action, client, ledger)
                done["repriced"] += 1
            elif action.kind == RESTOCK:
                _restock(action, client, ledger, location_id)
                done["restocked"] += 1
            elif action.kind == RETIRE:
                _retire(action, client, ledger, config)
                done["retired"] += 1
        except UserError as exc:
            log.error("%s %s: %s", action.kind, action.sku, exc)
            done["failed"] += 1
        except Exception as exc:  # One bad product must not stop the sync.
            log.exception("%s %s failed", action.kind, action.sku)
            done["failed"] += 1
            del exc
    ledger.save()
    return done


def _create(action: Action, client: ShopifyClient, ledger: Ledger, config,
            location_id: str, writer) -> None:
    item = action.item
    assert item is not None
    body = writer.describe(item) if writer else None
    title = (body.title if body else item.title)[:255]
    description = body.html if body else _plain_description(item)
    tags = sorted({config.order_tag, *item.tags, *( [config.vendor] if config.vendor else [])})

    created = client.mutate(
        queries.CREATE_PRODUCT,
        {
            "input": {
                "title": title,
                "handle": item.handle,
                "descriptionHtml": description,
                "vendor": item.vendor or config.vendor,
                "productType": item.product_type or config.product_type,
                "tags": tags,
                # New products start as drafts; publishing is a decision.
                "status": "DRAFT",
            }
        },
        field_name="productCreate",
    )
    product = created.get("product") or {}
    product_id = product.get("id", "")
    variant = _first_variant(product)
    variant_id = variant.get("id", "")
    inventory_item_id = (variant.get("inventoryItem") or {}).get("id", "")

    if product_id and variant_id:
        client.mutate(
            queries.UPDATE_VARIANTS,
            {
                "productId": product_id,
                "variants": [
                    {
                        "id": variant_id,
                        "price": action.price,
                        **({"compareAtPrice": action.compare_at}
                           if action.compare_at else {}),
                        "barcode": item.barcode or None,
                        "inventoryItem": {"tracked": True, "sku": item.sku},
                    }
                ],
            },
            field_name="productVariantsBulkUpdate",
        )
    if product_id and item.images:
        client.mutate(
            queries.ADD_MEDIA,
            {
                "productId": product_id,
                "media": [
                    {
                        "originalSource": url,
                        "mediaContentType": "IMAGE",
                        "alt": title[:120],
                    }
                    for url in item.images[:10]
                ],
            },
            field_name="productCreateMedia",
        )
    if inventory_item_id and location_id:
        _set_stock(client, inventory_item_id, location_id, item.quantity,
                   activate=True)

    ledger.remember(
        Entry(
            sku=item.sku,
            product_id=product_id,
            variant_id=variant_id,
            inventory_item_id=inventory_item_id,
            handle=item.handle,
            content_hash=content_hash(title, description, action.price),
            price=action.price,
            quantity=item.quantity,
        )
    )


def _reprice(action: Action, client: ShopifyClient, ledger: Ledger) -> None:
    client.mutate(
        queries.UPDATE_VARIANTS,
        {
            "productId": action.product_id,
            "variants": [
                {
                    "id": action.variant_id,
                    "price": action.price,
                    **({"compareAtPrice": action.compare_at}
                       if action.compare_at else {}),
                }
            ],
        },
        field_name="productVariantsBulkUpdate",
    )
    entry = ledger.get(action.sku) or Entry(sku=action.sku)
    entry.product_id = action.product_id or entry.product_id
    entry.variant_id = action.variant_id or entry.variant_id
    entry.price = action.price
    ledger.remember(entry)


def _restock(action: Action, client: ShopifyClient, ledger: Ledger,
             location_id: str) -> None:
    inventory_item_id = action.inventory_item_id or (
        (ledger.get(action.sku) or Entry(sku=action.sku)).inventory_item_id
    )
    if not (inventory_item_id and location_id):
        raise UserError(
            "inventorySetQuantities",
            [{"message": f"no inventory item or location for {action.sku}"}],
        )
    _set_stock(client, inventory_item_id, location_id, action.quantity)
    entry = ledger.get(action.sku) or Entry(sku=action.sku)
    entry.inventory_item_id = inventory_item_id
    entry.quantity = action.quantity
    ledger.remember(entry)


def _retire(action: Action, client: ShopifyClient, ledger: Ledger, config) -> None:
    status = "ARCHIVED" if config.on_missing == "archive" else "DRAFT"
    client.mutate(
        queries.UPDATE_PRODUCT,
        {"input": {"id": action.product_id, "status": status}},
        field_name="productUpdate",
    )
    entry = ledger.get(action.sku)
    if entry:
        entry.retired = True
        ledger.remember(entry)


def _set_stock(client: ShopifyClient, inventory_item_id: str, location_id: str,
               quantity: int, *, activate: bool = False) -> None:
    if activate:
        try:
            client.mutate(
                queries.ACTIVATE_INVENTORY,
                {"inventoryItemId": inventory_item_id, "locationId": location_id},
                field_name="inventoryActivate",
            )
        except UserError as exc:
            # Already stocked at this location is not a failure.
            if "already" not in str(exc).lower():
                raise
    client.mutate(
        queries.SET_INVENTORY,
        {
            "input": {
                "name": "available",
                "reason": "correction",
                "ignoreCompareQuantity": True,
                "quantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": location_id,
                        "quantity": max(0, int(quantity)),
                    }
                ],
            }
        },
        field_name="inventorySetQuantities",
    )


def _first_variant(product: dict) -> dict:
    edges = (product.get("variants") or {}).get("edges") or []
    return (edges[0].get("node") or {}) if edges else {}


def _plain_description(item: FeedItem) -> str:
    """A description built only from what the supplier actually said."""
    parts = []
    if item.description:
        parts.append(f"<p>{_escape(item.description)}</p>")
    facts = []
    if item.vendor:
        facts.append(f"<li>Brand: {_escape(item.vendor)}</li>")
    if item.product_type:
        facts.append(f"<li>Type: {_escape(item.product_type)}</li>")
    if item.barcode:
        facts.append(f"<li>Barcode: {_escape(item.barcode)}</li>")
    if facts:
        parts.append("<ul>" + "".join(facts) + "</ul>")
    return "".join(parts) or f"<p>{_escape(item.title)}</p>"


def _escape(text: str) -> str:
    import html

    return html.escape(str(text), quote=False)


def first_location(client: ShopifyClient, name: str = "") -> str:
    """The location to stock things at."""
    for location in client.paginate(queries.LOCATIONS, {}, path=["locations"]):
        if not location.get("isActive"):
            continue
        if not name or location.get("name", "").lower() == name.lower():
            return location.get("id", "")
    return ""

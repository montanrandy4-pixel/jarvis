"""Shop tools for JARVIS -- so you can ask how the store is doing.

Read-only on purpose. Applying a catalogue sync means dozens of writes that
cannot be summarised in a sentence, so that stays a deliberate `shop apply`
at a keyboard; asking what *would* change is safe and useful.
"""

from __future__ import annotations

import logging

from . import Tool, ToolResult

log = logging.getLogger("jarvis.shop")


def available() -> bool:
    """Whether this machine has a shop configured at all."""
    try:
        from shop.config import Config

        return bool(Config.load().store_domain)
    except Exception:
        return False


def _config():
    from shop.config import Config

    return Config.load()


def tools(_config_unused=None) -> list[Tool]:
    def shop_status() -> ToolResult:
        from shop.ledger import Ledger
        from shop.orders import low_stock

        config = _config()
        ledger = Ledger.load(config.ledger_path)
        if not ledger.entries:
            return ToolResult("The shop has nothing recorded yet.")
        live = ledger.live_skus
        lines = [
            f"{len(live)} products on sale, "
            f"{len(ledger.entries) - len(live)} retired."
        ]
        low = low_stock(ledger, config.low_stock_threshold)
        if low:
            listed = ", ".join(f"{sku} ({qty} left)" for sku, qty in low[:5])
            lines.append(f"{len(low)} low on stock: {listed}")
        else:
            lines.append("Nothing is low on stock.")
        return ToolResult("\n".join(lines), display="checked the shop")

    def shop_pending_changes() -> ToolResult:
        from shop import autopilot

        config = _config()
        config.dry_run = True  # Never write from a voice command.
        result = autopilot.once(config, catalogue=True, process_orders=False)
        if result.errors:
            return ToolResult("; ".join(result.errors), is_error=True)
        if not result.plan:
            return ToolResult("No catalogue feed is configured.")
        plan = result.plan
        if not plan.writes and not plan.of("hold"):
            return ToolResult("The store matches the supplier feed. Nothing to do.")
        lines = [plan.summary()]
        for action in plan.writes[:8]:
            lines.append(action.describe())
        held = plan.of("hold")
        if held:
            lines.append(f"{len(held)} held for review: " + "; ".join(
                f"{a.sku} ({a.reason})" for a in held[:3]
            ))
        return ToolResult("\n".join(lines), display="checked the supplier feed")

    def shop_recent_orders(limit: int = 10) -> ToolResult:
        from shop.client import ShopifyClient, ShopifyError
        from shop.ledger import Ledger
        from shop.orders import fetch_orders, triage

        config = _config()
        if config.problems():
            return ToolResult("; ".join(config.problems()), is_error=True)
        client = ShopifyClient(config.endpoint, config.token, dry_run=True)
        config._known_skus = Ledger.load(config.ledger_path).live_skus or None
        try:
            recent = fetch_orders(client, limit=max(1, min(int(limit), 25)),
                                  unseen_only=False)
        except ShopifyError as exc:
            return ToolResult(str(exc), is_error=True)
        if not recent:
            return ToolResult("No orders yet.")
        decided = [triage(order, config) for order in recent]
        flagged = [d for d in decided if d.needs_review]
        lines = [f"{len(decided)} recent orders."]
        if flagged:
            lines.append(f"{len(flagged)} need review:")
            lines.extend(f"  {d.describe()}" for d in flagged[:5])
        total = sum(d.total for d in decided)
        lines.append(f"Total value {total:.2f} {decided[0].currency}.")
        return ToolResult("\n".join(lines), display="checked recent orders")

    return [
        Tool(
            name="shop_status",
            description=(
                "How the shop is doing: how many products are on sale and what "
                "is running low on stock."
            ),
            input_schema={"type": "object", "properties": {}, "required": [],
                          "additionalProperties": False},
            handler=shop_status,
        ),
        Tool(
            name="shop_pending_changes",
            description=(
                "What the next catalogue sync would change -- new products, "
                "price moves, stock moves, and anything held for review. This "
                "only looks; it never changes the store."
            ),
            input_schema={"type": "object", "properties": {}, "required": [],
                          "additionalProperties": False},
            handler=shop_pending_changes,
        ),
        Tool(
            name="shop_recent_orders",
            description="Recent orders and whether any need a human's attention.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many (1-25)."}
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=shop_recent_orders,
        ),
    ]

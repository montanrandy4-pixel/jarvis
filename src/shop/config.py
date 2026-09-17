"""Configuration for the shop autopilot.

Read from ``shop.toml`` in the working directory (override with SHOP_CONFIG),
with secrets taken from the environment -- an access token belongs in neither
a config file nor a git repository.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Shopify ships a new API version each quarter and supports each for a year.
# Bump this when you upgrade; `shop doctor` tells you if it is no longer served.
DEFAULT_API_VERSION = "2026-01"

CONFIG_PATH = Path(os.environ.get("SHOP_CONFIG", "shop.toml"))


@dataclass
class PricingRules:
    """How a supplier's cost becomes a retail price."""

    # Multiplier applied to cost: 2.5 means a 60% margin.
    markup: float = 2.5
    # Add this after the markup, for handling or packaging.
    handling: float = 0.0
    # Round up to the nearest .99, .95 or nothing at all.
    charm_ending: str = "0.99"
    # Never sell below this, whatever the rules produce.
    floor: float = 0.0
    # Refuse to publish anything above this without review.
    ceiling: float = 0.0
    # Show a struck-through "was" price at this multiple of the retail price.
    compare_at_multiplier: float = 0.0
    # Ignore feed price moves smaller than this, to avoid churning the store.
    min_change: float = 0.01

    @classmethod
    def from_dict(cls, data: dict) -> "PricingRules":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Config:
    # --- Store ---
    # your-store.myshopify.com (not the custom domain)
    store_domain: str = ""
    # Name of the environment variable holding the Admin API access token.
    token_env: str = "SHOPIFY_ADMIN_TOKEN"
    api_version: str = DEFAULT_API_VERSION
    location: str = ""  # Inventory location name; blank means the first one.
    # Point at something other than the live store: a mock, a proxy, or a
    # development store behind one. Set SHOP_ENDPOINT to override per run.
    endpoint_override: str = ""

    # --- Catalog source ---
    feed_path: str = ""  # CSV or JSON file, or an http(s) URL.
    vendor: str = ""
    product_type: str = ""
    collection: str = ""  # Collection to file synced products under.
    # Products missing from the feed: unpublish (safe) or archive.
    on_missing: str = "unpublish"
    # Never touch products that were not created by the autopilot.
    manage_only_own: bool = True

    # --- Pricing ---
    pricing: PricingRules = field(default_factory=PricingRules)

    # --- Orders ---
    process_orders: bool = True
    order_tag: str = "autopilot"
    # Tag orders for manual review above this value rather than auto-fulfilling.
    review_above: float = 250.0
    auto_fulfill: bool = False  # Off by default: fulfilling is irreversible.
    low_stock_threshold: int = 3

    # --- Copy ---
    # Write titles, descriptions and SEO with the local model (see jarvis).
    ai_copy: bool = True
    brand_voice: str = "plain, specific, no hype"

    # --- Running ---
    dry_run: bool = True  # Nothing is written until you turn this off.
    interval_minutes: int = 60
    state_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("SHOP_STATE_DIR", "~/.local/state/shop")
        ).expanduser()
    )

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")

    @property
    def endpoint(self) -> str:
        override = os.environ.get("SHOP_ENDPOINT", self.endpoint_override)
        if override:
            return override
        return (
            f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"
        )

    @property
    def ledger_path(self) -> Path:
        """Where the autopilot records what it has already done."""
        return self.state_dir / "ledger.json"

    @classmethod
    def load(cls, path: Path | None = None, **overrides) -> "Config":
        data: dict = {}
        source = Path(path or CONFIG_PATH)
        if source.is_file():
            with source.open("rb") as handle:
                raw = tomllib.load(handle)
            data = raw.get("shop", raw)

        pricing = PricingRules.from_dict(data.pop("pricing", {}) or {})
        known = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in data.items() if k in known and k != "pricing"}
        clean.update({k: v for k, v in overrides.items() if v is not None})
        # An explicit pricing override wins over the file's [shop.pricing].
        override = clean.pop("pricing", None)
        if isinstance(override, PricingRules):
            pricing = override
        elif isinstance(override, dict):
            pricing = PricingRules.from_dict(override)
        if "state_dir" in clean:
            clean["state_dir"] = Path(clean["state_dir"]).expanduser()
        return cls(pricing=pricing, **clean)

    def problems(self) -> list[str]:
        """Everything that would stop a real run, in the order worth fixing."""
        issues = []
        if not self.store_domain:
            issues.append("store_domain is not set (your-store.myshopify.com)")
        elif not self.store_domain.endswith(".myshopify.com"):
            issues.append(
                f"store_domain should be the myshopify.com address, got "
                f"{self.store_domain!r}"
            )
        if not self.token:
            issues.append(
                f"no access token: set {self.token_env} in the environment"
            )
        if not self.feed_path:
            issues.append("feed_path is not set (the supplier CSV or JSON)")
        if self.pricing.markup <= 0:
            issues.append("pricing.markup must be greater than zero")
        return issues

"""Configuration for the storefront agent.

Reuses ``shop.toml`` rather than inventing a second config file: the store
domain and token are the same store. Storefront-specific settings live under
a ``[storefront]`` table, so the two agents can share a file without either
reading the other's keys.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_API_VERSION = "2026-01"
CONFIG_PATH = Path(os.environ.get("SHOP_CONFIG", "shop.toml"))


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "storefront"


@dataclass
class Config:
    store_domain: str = ""
    token_env: str = "SHOPIFY_ADMIN_TOKEN"
    api_version: str = DEFAULT_API_VERSION
    endpoint_override: str = ""

    # Flag an order at or above this. On a digital store an unusually large
    # order more often means card testing than a good day.
    review_above: float = 250.0
    # Where the files live, so `attach` can find them by name.
    assets_dir: str = ""
    # Map SKU -> filename, relative to assets_dir.
    assets: dict[str, str] = field(default_factory=dict)

    dry_run: bool = True
    interval_minutes: int = 60
    state_dir: Path = field(default_factory=_state_dir)

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")

    @property
    def endpoint(self) -> str:
        override = os.environ.get("SHOP_ENDPOINT", self.endpoint_override)
        if override:
            return override
        return (
            f"https://{self.store_domain}/admin/api/"
            f"{self.api_version}/graphql.json"
        )

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "assets.json"

    def resolve_assets(self) -> dict[str, Path]:
        base = Path(self.assets_dir).expanduser() if self.assets_dir else Path.cwd()
        return {sku: base / name for sku, name in self.assets.items()}

    def validate(self) -> list[str]:
        problems = []
        if not self.store_domain:
            problems.append("no store_domain: set it in shop.toml under [shop]")
        if not self.token:
            problems.append(
                f"no access token: set {self.token_env} in the environment"
            )
        return problems

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        data: dict = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text("utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise SystemExit(f"could not read {path}: {exc}") from exc

        shop = data.get("shop") or {}
        mine = data.get("storefront") or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}

        merged = {
            k: v
            for k, v in {**shop, **mine}.items()
            if k in known and k != "state_dir"
        }
        config = cls(**merged)
        if sd := (mine.get("state_dir") or shop.get("state_dir")):
            config.state_dir = Path(sd).expanduser()
        return config

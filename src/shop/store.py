"""The whole store, as a file.

`shop build` reads this and makes the store match it: collections, pages,
policies, navigation and the products from the feed. It is declarative and
idempotent, so editing the file and running build again edits the store.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Brand:
    name: str = "My Shop"
    tagline: str = ""
    # Used in generated copy and in the preview.
    about: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    currency: str = "GBP"
    # Preview styling only; Shopify themes are configured in the admin.
    accent: str = "#1f6f5c"


@dataclass
class Collection:
    handle: str
    title: str
    description: str = ""
    # A smart collection built from a tag, or a manual one when empty.
    tag: str = ""
    product_type: str = ""
    featured: bool = False


@dataclass
class Page:
    handle: str
    title: str
    body: str = ""
    # Pages the store needs but that are not in the main menu.
    hidden: bool = False


@dataclass
class Policy:
    kind: str  # refund | privacy | terms | shipping | contact
    body: str = ""


@dataclass
class MenuItem:
    title: str
    target: str  # a handle, or an absolute path such as /collections/all


@dataclass
class StoreSpec:
    brand: Brand = field(default_factory=Brand)
    collections: list[Collection] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    main_menu: list[MenuItem] = field(default_factory=list)
    footer_menu: list[MenuItem] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "StoreSpec":
        source = Path(path).expanduser()
        if not source.is_file():
            raise ValueError(f"no store definition at {source}")
        with source.open("rb") as handle:
            data = tomllib.load(handle)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "StoreSpec":
        brand_data = data.get("brand", {})
        known = {f.name for f in Brand.__dataclass_fields__.values()}
        brand = Brand(**{k: v for k, v in brand_data.items() if k in known})

        collections = [
            Collection(
                handle=c.get("handle") or _slug(c.get("title", "")),
                title=c.get("title", ""),
                description=c.get("description", ""),
                tag=c.get("tag", ""),
                product_type=c.get("product_type", ""),
                featured=bool(c.get("featured")),
            )
            for c in data.get("collections", [])
            if c.get("title")
        ]
        pages = [
            Page(
                handle=p.get("handle") or _slug(p.get("title", "")),
                title=p.get("title", ""),
                body=p.get("body", ""),
                hidden=bool(p.get("hidden")),
            )
            for p in data.get("pages", [])
            if p.get("title")
        ]
        policies = [
            Policy(kind=kind, body=body)
            for kind, body in (data.get("policies", {}) or {}).items()
            if str(body).strip()
        ]
        navigation = data.get("navigation", {}) or {}
        return cls(
            brand=brand,
            collections=collections,
            pages=pages,
            policies=policies,
            main_menu=_menu(navigation.get("main", [])),
            footer_menu=_menu(navigation.get("footer", [])),
        )

    def problems(self) -> list[str]:
        issues = []
        if not self.brand.name or self.brand.name == "My Shop":
            issues.append("brand.name is still the placeholder")
        if not self.brand.email:
            issues.append("brand.email is not set (customers need a way to reach you)")
        wanted = {"refund", "privacy", "terms", "shipping"}
        have = {p.kind for p in self.policies}
        for missing in sorted(wanted - have):
            issues.append(f"no {missing} policy")
        handles = [c.handle for c in self.collections]
        if len(handles) != len(set(handles)):
            issues.append("two collections share a handle")
        return issues


def _menu(items) -> list[MenuItem]:
    out = []
    for item in items or []:
        if isinstance(item, dict) and item.get("title"):
            out.append(
                MenuItem(title=item["title"], target=item.get("target", ""))
            )
    return out


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")

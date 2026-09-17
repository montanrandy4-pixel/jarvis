"""Reading a supplier's product feed.

Feeds arrive as whatever the supplier felt like: CSV or JSON, columns named
``sku`` or ``Item Number`` or ``ITEM_NO``, prices with currency symbols, stock
as a number or the word "in stock". This module turns that into a list of
:class:`FeedItem`, and says clearly which rows it had to reject.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.backends._http import HTTPError, fetch_text

log = logging.getLogger("shop.feed")

# Column aliases, in order of preference. Matching ignores case, spaces,
# underscores and hyphens.
ALIASES = {
    "sku": ["sku", "itemnumber", "itemno", "item", "partnumber", "mpn", "id",
            "productid", "productcode", "code"],
    "title": ["title", "name", "productname", "product", "description short",
              "itemname", "shortdescription"],
    "description": ["description", "longdescription", "details", "body",
                    "productdescription", "features"],
    "cost": ["cost", "wholesale", "wholesaleprice", "costprice", "unitcost",
             "dealerprice", "yourprice", "netprice"],
    "price": ["price", "retail", "retailprice", "msrp", "rrp", "listprice"],
    "quantity": ["quantity", "qty", "stock", "inventory", "available",
                 "instock", "stocklevel", "onhand"],
    "images": ["images", "image", "imageurl", "imageurls", "picture", "photo",
               "imagelink", "mainimage"],
    "vendor": ["vendor", "brand", "manufacturer", "supplier", "make"],
    "product_type": ["producttype", "type", "category", "productcategory",
                     "department"],
    "barcode": ["barcode", "upc", "ean", "gtin", "isbn"],
    "weight": ["weight", "shippingweight", "weightkg", "weightlb"],
    "tags": ["tags", "keywords", "labels"],
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MONEY = re.compile(r"-?\d+(?:[.,]\d+)?")
_IN_STOCK = {"in stock", "instock", "available", "yes", "true", "y", "in-stock"}
_OUT_OF_STOCK = {"out of stock", "outofstock", "unavailable", "no", "false",
                 "n", "backorder", "discontinued", "sold out"}


@dataclass
class FeedItem:
    """One product as the supplier describes it."""

    sku: str
    title: str
    cost: float
    quantity: int = 0
    description: str = ""
    price: float | None = None  # Supplier's suggested retail, if given.
    images: list[str] = field(default_factory=list)
    vendor: str = ""
    product_type: str = ""
    barcode: str = ""
    weight: float = 0.0
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def handle(self) -> str:
        """The store URL slug. Derived from the SKU so it is stable."""
        return slugify(f"{self.title}-{self.sku}")[:255]


@dataclass
class FeedReport:
    items: list[FeedItem]
    rejected: list[tuple[int, str]] = field(default_factory=list)
    columns: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.items)

    def summary(self) -> str:
        line = f"{len(self.items)} products read"
        if self.rejected:
            line += f", {len(self.rejected)} rows rejected"
        return line


def normalise(name: str) -> str:
    return _NON_ALNUM.sub("", str(name).strip().lower())


def slugify(text: str) -> str:
    slug = _NON_ALNUM.sub("-", str(text).strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def parse_money(value) -> float | None:
    """Pull a number out of '£12.99', '12,99 EUR', '1,299.00' or 12.99."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    # Strip thousands separators only when they are clearly that.
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    match = _MONEY.search(text)
    if not match:
        return None
    number = match.group(0).replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None


def parse_quantity(value) -> int:
    """Turn a stock column into a number, including the wordy kinds."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 10 if value else 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower()
    if not text:
        return 0
    if text in _IN_STOCK:
        return 10  # A sensible default when the supplier is vague.
    if text in _OUT_OF_STOCK:
        return 0
    number = parse_money(text)
    return max(0, int(number)) if number is not None else 0


def split_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    parts = re.split(r"[|,;\n]+", str(value))
    return [p.strip() for p in parts if p.strip()]


def map_columns(headers, overrides: dict | None = None) -> dict:
    """Work out which column holds which field."""
    overrides = {k: normalise(v) for k, v in (overrides or {}).items()}
    available = {normalise(h): h for h in headers if h}
    mapping: dict[str, str] = {}
    for field_name, candidates in ALIASES.items():
        if field_name in overrides and overrides[field_name] in available:
            mapping[field_name] = available[overrides[field_name]]
            continue
        for candidate in candidates:
            if candidate in available:
                mapping[field_name] = available[candidate]
                break
    return mapping


def read(source: str, *, column_map: dict | None = None) -> FeedReport:
    """Read a feed from a file path or URL."""
    text = _load(source)
    rows = _rows(text, source)
    if not rows:
        return FeedReport([], [(0, "the feed contained no rows")])

    headers = list(rows[0].keys())
    mapping = map_columns(headers, column_map)
    missing = [f for f in ("sku", "title", "cost") if f not in mapping]
    # A feed with only a retail price is still usable: treat it as the cost.
    if "cost" in missing and "price" in mapping:
        mapping["cost"] = mapping["price"]
        missing.remove("cost")
    if missing:
        raise ValueError(
            f"the feed has no column for: {', '.join(missing)}. "
            f"Columns found: {', '.join(headers[:12])}"
        )

    items, rejected = [], []
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):  # Row 1 is the header.
        try:
            item = _to_item(row, mapping)
        except ValueError as exc:
            rejected.append((number, str(exc)))
            continue
        if item.sku in seen:
            rejected.append((number, f"duplicate sku {item.sku}"))
            continue
        seen.add(item.sku)
        items.append(item)
    return FeedReport(items, rejected, mapping)


def _load(source: str) -> str:
    if str(source).startswith(("http://", "https://")):
        try:
            return fetch_text(source, max_bytes=20_000_000)
        except HTTPError as exc:
            raise ValueError(f"could not download the feed: {exc}") from exc
    path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError(f"no feed file at {path}")
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _rows(text: str, source: str) -> list[dict]:
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        data = json.loads(stripped)
        if isinstance(data, dict):
            for key in ("products", "items", "data", "results", "rows"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        return [row for row in data if isinstance(row, dict)]
    # CSV, with the delimiter sniffed -- suppliers love semicolons and tabs.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _to_item(row: dict, mapping: dict) -> FeedItem:
    def value(field_name, default=None):
        column = mapping.get(field_name)
        return row.get(column, default) if column else default

    sku = str(value("sku") or "").strip()
    if not sku:
        raise ValueError("no sku")
    title = str(value("title") or "").strip()
    if not title:
        raise ValueError(f"{sku} has no title")
    cost = parse_money(value("cost"))
    if cost is None:
        raise ValueError(f"{sku} has no usable cost (got {value('cost')!r})")
    if cost < 0:
        raise ValueError(f"{sku} has a negative cost")

    return FeedItem(
        sku=sku,
        title=title,
        cost=cost,
        quantity=parse_quantity(value("quantity")),
        description=str(value("description") or "").strip(),
        price=parse_money(value("price")),
        images=split_list(value("images")),
        vendor=str(value("vendor") or "").strip(),
        product_type=str(value("product_type") or "").strip(),
        barcode=str(value("barcode") or "").strip(),
        weight=parse_money(value("weight")) or 0.0,
        tags=split_list(value("tags")),
    )

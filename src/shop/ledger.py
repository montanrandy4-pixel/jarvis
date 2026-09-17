"""What the autopilot has already done.

Records the Shopify ids it created for each SKU and a hash of the content it
last wrote, so a repeated run is a no-op rather than a duplicate store.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Entry:
    sku: str
    product_id: str = ""
    variant_id: str = ""
    inventory_item_id: str = ""
    handle: str = ""
    content_hash: str = ""
    price: str = ""
    quantity: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    retired: bool = False


@dataclass
class Ledger:
    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        ledger = cls(Path(path))
        if not ledger.path.is_file():
            return ledger
        try:
            raw = json.loads(ledger.path.read_text())
        except (json.JSONDecodeError, OSError):
            # A damaged ledger must not cause a second copy of the catalogue;
            # keep it for inspection and rebuild from the store instead.
            try:
                ledger.path.replace(ledger.path.with_suffix(".corrupt"))
            except OSError:
                pass
            return ledger
        for item in raw.get("skus", []):
            try:
                ledger.entries[item["sku"]] = Entry(**item)
            except (KeyError, TypeError):
                continue
        return ledger

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "skus": [asdict(e) for e in self.entries.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)

    def get(self, sku: str) -> Entry | None:
        return self.entries.get(sku)

    def remember(self, entry: Entry) -> None:
        entry.updated_at = time.time()
        if not entry.created_at:
            entry.created_at = entry.updated_at
        self.entries[entry.sku] = entry

    @property
    def live_skus(self) -> set[str]:
        return {s for s, e in self.entries.items() if not e.retired}


def content_hash(*parts) -> str:
    """A stable fingerprint of the content written for a product."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]

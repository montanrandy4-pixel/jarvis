"""What the agent knows about attached files.

Shopify's Digital Products app keeps its attachments in its own storage,
reachable through the app's admin UI and not through the Admin API. So the
agent cannot ask Shopify whether a product has a file. It has to remember.

This is that memory: a SKU, the file that was attached, its size and digest,
and when. The digest is what makes it more than a checklist -- if you rebuild
a product file and forget to re-upload it, the digest stops matching and the
audit says so.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path, _chunk: int = 1 << 20) -> str:
    """SHA-256 of a file, read in chunks so a large bundle does not sit in RAM."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_chunk):
            h.update(block)
    return h.hexdigest()


@dataclass
class Asset:
    sku: str
    filename: str
    sha256: str
    bytes: int
    attached_at: str
    source_path: str = ""

    @classmethod
    def from_file(cls, sku: str, path: Path) -> "Asset":
        path = Path(path)
        return cls(
            sku=sku,
            filename=path.name,
            sha256=digest(path),
            bytes=path.stat().st_size,
            attached_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_path=str(path.resolve()),
        )

    def stale_against(self, path: Path) -> bool:
        """True when the file on disk no longer matches what was uploaded."""
        try:
            return digest(Path(path)) != self.sha256
        except OSError:
            return False


@dataclass
class AssetLedger:
    path: Path
    assets: dict[str, Asset] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "AssetLedger":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt ledger must not stop the agent reporting on the store.
            # Treat it as empty; the audit will simply say nothing is attached.
            return cls(path=path)
        return cls(
            path=path,
            assets={k: Asset(**v) for k, v in (raw.get("assets") or {}).items()},
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "assets": {k: asdict(v) for k, v in sorted(self.assets.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
        tmp.replace(self.path)

    def remember(self, asset: Asset) -> None:
        self.assets[asset.sku] = asset

    def forget(self, sku: str) -> bool:
        return self.assets.pop(sku, None) is not None

    @property
    def skus(self) -> set[str]:
        return set(self.assets)

    def stale(self) -> list[Asset]:
        """Assets whose source file has changed since it was uploaded."""
        out = []
        for asset in self.assets.values():
            if asset.source_path and asset.stale_against(Path(asset.source_path)):
                out.append(asset)
        return out

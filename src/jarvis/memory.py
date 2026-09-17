"""Long-term memory: a small, human-readable JSON file of facts.

Deliberately not a vector store. A personal assistant accumulates dozens of
facts, not millions, and the whole set fits comfortably in the system prompt --
which means recall is exact and the user can read (and delete) everything with a
text editor.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Memory:
    id: str
    text: str
    created_at: float
    tags: list[str]

    def describe(self) -> str:
        return f"[{self.id}] {self.text}" + (
            f" (tags: {', '.join(self.tags)})" if self.tags else ""
        )


class MemoryStore:
    """Append-mostly store of facts, persisted as JSON on every write."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: list[Memory] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # A corrupt memory file must not stop JARVIS from booting. Keep the
            # damaged copy aside so nothing is silently destroyed.
            backup = self.path.with_suffix(".corrupt")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return
        for item in raw.get("memories", []):
            try:
                self._items.append(
                    Memory(
                        id=item["id"],
                        text=item["text"],
                        created_at=float(item.get("created_at", 0)),
                        tags=list(item.get("tags", [])),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # Skip malformed entries, keep the rest.

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "memories": [asdict(m) for m in self._items]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)  # Atomic, so a crash cannot truncate the file.

    def all(self) -> list[Memory]:
        return list(self._items)

    def texts(self) -> list[str]:
        return [m.text for m in self._items]

    def add(self, text: str, tags: list[str] | None = None) -> Memory:
        text = text.strip()
        for existing in self._items:
            if existing.text.lower() == text.lower():
                return existing  # Saying it twice should not store it twice.
        item = Memory(
            id=uuid.uuid4().hex[:8],
            text=text,
            created_at=time.time(),
            tags=list(tags or []),
        )
        self._items.append(item)
        self._save()
        return item

    def search(self, query: str) -> list[Memory]:
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return self.all()
        scored = []
        for item in self._items:
            haystack = (item.text + " " + " ".join(item.tags)).lower()
            hits = sum(1 for t in terms if t in haystack)
            if hits:
                scored.append((hits, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored]

    def forget(self, memory_id: str) -> bool:
        before = len(self._items)
        self._items = [m for m in self._items if m.id != memory_id]
        if len(self._items) != before:
            self._save()
            return True
        return False

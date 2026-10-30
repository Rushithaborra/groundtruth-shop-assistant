"""Learned synonyms: shopper words mapped to catalogue product types, remembered so a model is asked only once.

A word the rules can't place ("joota") is resolved by a model the first time. The mapping is stored here as
*proposed*, and counts how often it was seen. A person reviews proposals (`python main.py synonyms`); only
*approved* ones are used, by the plain rule path, at zero tokens and no model call. So the cost of the AI falls as the
shop learns its shoppers' words, and a wrong mapping can never take effect without a person saying so.

The file is small JSON: {word: {"target", "status", "seen", "source"}}. Reads and writes are thread-safe, and writes
are atomic (temp file, then rename), so a crash can't leave half a file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .catalogue import CatalogueIndex
    from .understanding import Parsed

logger = logging.getLogger(__name__)

PROPOSED, APPROVED, REJECTED = "proposed", "approved", "rejected"


class LearnedSynonyms:
    """The reviewed word -> product-type table. With no path it lives in memory only (used by tests)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}
        if self.path and self.path.exists():
            self._load()

    # -- storage -------------------------------------------------------------------------------
    def _load(self) -> None:
        """Read the file; a broken or unreadable one is logged and treated as empty, never fatal."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("synonym file %s unreadable (%s); starting empty", self.path, exc)
            return
        if isinstance(data, dict):
            self._entries = {
                str(w): e
                for w, e in data.items()
                if isinstance(e, dict)
                and isinstance(e.get("target"), str)
                and e.get("status") in (PROPOSED, APPROVED, REJECTED)
            }

    def save(self) -> None:
        """Write the table atomically (no-op for an in-memory table)."""
        if not self.path:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._entries, fh, ensure_ascii=False, indent=1, sort_keys=True)
                os.replace(tmp, self.path)
            except OSError:
                logger.exception("could not save %s", self.path)
                Path(tmp).unlink(missing_ok=True)

    # -- use -----------------------------------------------------------------------------------
    def lookup(self, word: str) -> str | None:
        """The approved product type for a word, or None."""
        with self._lock:
            entry = self._entries.get(word.lower())
            return entry["target"] if entry and entry["status"] == APPROVED else None

    def apply(self, parsed: Parsed, index: CatalogueIndex) -> bool:
        """Map unknown words with an approved synonym onto their product type, in place. True if anything changed.
        A target the catalogue no longer has is ignored, so a stale entry can't point at nothing."""
        changed = False
        for word in list(parsed.unknown):
            target = None if word in parsed.external else self.lookup(word)
            if target is None or target not in index.type_vocab:
                continue
            parsed.unknown.remove(word)
            if target not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(target)
            parsed.mapped.append((word, target))
            changed = True
        return changed

    # -- learning ------------------------------------------------------------------------------
    def propose(self, word: str, target: str, source: str) -> None:
        """Remember that a model read `word` as `target`. Seen again, the count goes up; an approved or rejected
        entry is never overwritten by a model."""
        word = word.lower().strip()
        if not word or " " in word or word == target:
            return
        with self._lock:
            entry = self._entries.get(word)
            if entry is None:
                self._entries[word] = {"target": target, "status": PROPOSED, "seen": 1, "source": source}
            elif entry["target"] == target:
                entry["seen"] = int(entry.get("seen", 0)) + 1
            self.save()

    def approve(self, word: str, target: str | None = None) -> bool:
        """Make a synonym live (optionally changing its target). False if the word is unknown."""
        with self._lock:
            entry = self._entries.get(word.lower())
            if entry is None and target:
                entry = self._entries[word.lower()] = {
                    "target": target,
                    "status": PROPOSED,
                    "seen": 0,
                    "source": "manual",
                }
            if entry is None:
                return False
            entry["status"] = APPROVED
            if target:
                entry["target"] = target
            self.save()
            return True

    def reject(self, word: str) -> bool:
        """Mark a proposal as wrong so it is never used or re-proposed."""
        with self._lock:
            entry = self._entries.get(word.lower())
            if entry is None:
                return False
            entry["status"] = REJECTED
            self.save()
            return True

    def pending(self) -> list[tuple[str, dict]]:
        """Proposals waiting for review, most often seen first."""
        with self._lock:
            rows = [(w, dict(e)) for w, e in self._entries.items() if e["status"] == PROPOSED]
        return sorted(rows, key=lambda r: (-int(r[1].get("seen", 0)), r[0]))

    def counts(self) -> dict[str, int]:
        """How many entries are in each state."""
        with self._lock:
            out = {PROPOSED: 0, APPROVED: 0, REJECTED: 0}
            for e in self._entries.values():
                out[e["status"]] += 1
            return out

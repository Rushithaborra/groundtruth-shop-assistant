"""Per-user session storage for serving many users: bounded (LRU), expiring (TTL) and thread-safe."""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock

from ..config import get_settings
from .assistant import ShoppingAssistant
from .response import Response, Session


class SessionStore:
    def __init__(self, max_sessions: int | None = None, ttl_seconds: float | None = None) -> None:
        limits = get_settings().sessions
        self.max_sessions = max_sessions if max_sessions is not None else limits.max_sessions
        self.ttl = ttl_seconds if ttl_seconds is not None else limits.ttl_seconds
        self._data: OrderedDict[str, tuple[float, Session, Lock]] = OrderedDict()
        self._lock = Lock()

    def _get(self, user_id: str) -> tuple[Session, Lock]:
        now = time.monotonic()
        with self._lock:
            entry = self._data.pop(user_id, None)
            if entry is None or now - entry[0] >= self.ttl:
                entry = (now, Session(), Lock())
            self._data[user_id] = (now, entry[1], entry[2])  # most recently used goes last
            session, lock = entry[1], entry[2]
            while len(self._data) > self.max_sessions or (
                self._data and next(iter(self._data.values()))[0] + self.ttl <= now
            ):
                self._data.popitem(last=False)  # evict least recently used / expired
            return session, lock

    def ask(self, assistant: ShoppingAssistant, user_id: str, query: str) -> Response:
        """Answer one user's query; requests from the same user are serialised, different users run in parallel."""
        session, lock = self._get(user_id)
        with lock:
            return assistant.ask(query, session)

    def __len__(self) -> int:
        return len(self._data)

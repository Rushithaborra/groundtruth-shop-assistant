"""Session storage behind one small interface, so the web layer doesn't care where a chat is kept.

A visitor's chat state is a few hundred bytes of JSON: the current search (filters, budget, size), the last product,
the last listing, and the last few messages. Two backends keep it:

* `MemoryBackend`: in this process only; gone on restart. Fastest, and right for one process.
* `SqliteBackend`: one SQLite file (standard library, no server to run). It survives restarts, and any number of
  server processes can use the same file, so a visitor's next message may land on a different process and still
  continue the chat. Write-ahead logging lets readers and one writer work at the same time.

Both cap how many sessions they keep (least recently used go first) and expire idle ones. Another store (Redis, say)
needs only the four methods of `SessionBackend`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import SessionSettings


class SessionBackend(Protocol):
    """What the web layer needs from a session store."""

    def load(self, sid: str) -> dict | None:
        """The stored state for a session id, or None when it is unknown or expired."""
        ...

    def save(self, sid: str, data: dict) -> None:
        """Store (or replace) a session's state and mark it just used."""
        ...

    def delete(self, sid: str) -> None:
        """Forget a session."""
        ...

    def __len__(self) -> int:
        """How many sessions are kept."""
        ...

    def close(self) -> None:
        """Release anything held open (files, connections)."""
        ...


class MemoryBackend:
    """Sessions in this process. State is stored as JSON text, so a stored session can't be changed by accident and
    behaves exactly like one read from a file."""

    def __init__(self, max_sessions: int, ttl_seconds: float) -> None:
        self.max_sessions, self.ttl = max_sessions, ttl_seconds
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def load(self, sid: str) -> dict | None:
        """The session, refreshed as recently used; None when unknown or idle for longer than the expiry."""
        with self._lock:
            entry = self._data.get(sid)
            if entry is None:
                return None
            if time.monotonic() - entry[0] > self.ttl:
                del self._data[sid]
                return None
            self._data.move_to_end(sid)
            return json.loads(entry[1])

    def save(self, sid: str, data: dict) -> None:
        """Store the session; the least recently used go when there are more than the cap."""
        with self._lock:
            self._data[sid] = (time.monotonic(), json.dumps(data))
            self._data.move_to_end(sid)
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)

    def delete(self, sid: str) -> None:
        """Forget a session."""
        with self._lock:
            self._data.pop(sid, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def close(self) -> None:
        """Nothing is held open."""


class SqliteBackend:
    """Sessions in a SQLite file, safe for many threads and many processes."""

    TRIM_EVERY = 200  # saves between clean-ups of expired and surplus sessions

    def __init__(self, path: str | Path, max_sessions: int, ttl_seconds: float) -> None:
        self.path, self.max_sessions, self.ttl = Path(path), max_sessions, ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()  # one connection per thread: SQLite connections aren't shared across threads
        self._connections: list[sqlite3.Connection] = []  # all of them, so close() can release every file handle
        self._connections_lock = threading.Lock()
        self._saves = 0
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS sessions_updated ON sessions (updated)")

    def _db(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use with write-ahead logging and a wait for busy writers."""
        db = getattr(self._local, "db", None)
        if db is None:
            # autocommit: each statement is atomic. A connection is only used by the thread that opened it; the flag
            # just lets close() shut them all from one thread.
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            with self._connections_lock:
                self._connections.append(db)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA busy_timeout=10000")
            self._local.db = db
        return db

    def load(self, sid: str) -> dict | None:
        """The session, or None when unknown or idle for longer than the expiry (which also removes it)."""
        row = self._db().execute("SELECT data, updated FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        if time.time() - row[1] > self.ttl:
            self.delete(sid)
            return None
        return json.loads(row[0])

    def save(self, sid: str, data: dict) -> None:
        """Insert or replace the session; every so often, drop expired sessions and the surplus beyond the cap."""
        self._db().execute(
            "INSERT INTO sessions (id, data, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated = excluded.updated",
            (sid, json.dumps(data), time.time()),
        )
        self._saves += 1
        if self._saves % self.TRIM_EVERY == 0:
            self.trim()

    def trim(self) -> None:
        """Remove expired sessions, then the least recently used beyond `max_sessions`."""
        db = self._db()
        db.execute("DELETE FROM sessions WHERE updated < ?", (time.time() - self.ttl,))
        db.execute(
            "DELETE FROM sessions WHERE id IN (SELECT id FROM sessions ORDER BY updated DESC LIMIT -1 OFFSET ?)",
            (self.max_sessions,),
        )

    def delete(self, sid: str) -> None:
        """Forget a session."""
        self._db().execute("DELETE FROM sessions WHERE id = ?", (sid,))

    def __len__(self) -> int:
        return self._db().execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def close(self) -> None:
        """Close every connection this store opened (needed before the file can be deleted on Windows)."""
        with self._connections_lock:
            for db in self._connections:
                db.close()
            self._connections.clear()
        self._local = threading.local()


def open_backend(settings: SessionSettings, max_sessions: int | None = None, kind: str | None = None) -> SessionBackend:
    """The session store the settings name ("memory" or "sqlite"); `kind` overrides the setting."""
    kind = kind or settings.backend
    cap = max_sessions if max_sessions is not None else settings.max_sessions
    if kind == "memory":
        return MemoryBackend(cap, settings.ttl_seconds)
    if kind == "sqlite":
        return SqliteBackend(settings.sqlite_path, cap, settings.ttl_seconds)
    raise ValueError(f"unknown session backend {kind!r}: use 'memory' or 'sqlite'")

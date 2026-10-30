"""Where chat sessions live: in the process, or in a SQLite file that survives restarts and is shared by processes."""

from .sessions import MemoryBackend, SessionBackend, SqliteBackend, open_backend

__all__ = ["MemoryBackend", "SessionBackend", "SqliteBackend", "open_backend"]

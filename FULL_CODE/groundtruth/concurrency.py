"""A small reader-writer lock: many questions at once, but a catalogue update runs alone."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class ReadWriteLock:
    """Any number of readers, or one writer. A waiting writer stops new readers, so updates are never starved."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Hold the lock for reading (answering a question)."""
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Hold the lock for writing (changing the catalogue)."""
        with self._cond:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()

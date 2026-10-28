"""Terminal set-up: logging for events and diagnostics, and stdout for a command's own output."""

from __future__ import annotations

import logging
import sys


def configure_console(level: str) -> None:
    """Send log records to stderr and make stdout UTF-8 (prices are printed with the ₹ sign)."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def write_out(text: str = "") -> None:
    """A line of the command's own output (a chat reply, a report table), as opposed to a log record."""
    sys.stdout.write(f"{text}\n")

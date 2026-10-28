"""Errors whose message is meant for the person running a command: `main.py` prints it in one line, no traceback.

Programming errors are not GroundTruthError and keep their full traceback.
"""

from __future__ import annotations


class GroundTruthError(Exception):
    """Something the user can fix: a missing or unreadable data file, a failed check, a missing library."""


class DataFileError(GroundTruthError, ValueError):
    """A catalogue, policy or question file is missing, unreadable, or holds nothing usable."""

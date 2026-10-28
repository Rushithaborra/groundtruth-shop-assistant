"""Where the code lives. Data and output locations are settings: see config.json."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # FULL_CODE/
WEB_DIR = ROOT / "web"

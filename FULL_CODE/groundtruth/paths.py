"""Project paths, resolved from this file so commands work from any working directory."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # FULL_CODE/
DATA_DIR = ROOT / "data"
SCALE_DIR = DATA_DIR / "scale"
OUTPUT_DIR = ROOT / "output"
WEB_DIR = ROOT / "web"

"""Download the offline speech-to-text model for the web demo's mic button, once, while online.

    uv sync --extra voice                      # the optional speech library (faster-whisper)
    uv run python main.py voice-model          # the "base" model, into the folder config.json names (~150 MB)

After this the mic button appears in `main.py serve`, and voice works with no internet.
"""

from __future__ import annotations

import argparse
import logging
import os

from groundtruth.config import get_settings
from groundtruth.errors import GroundTruthError

logger = logging.getLogger(__name__)
MODEL = "Systran/faster-whisper-base"  # Whisper "base", converted for faster-whisper (MIT licence)


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The voice-model command's option: which model to download."""
    ap.add_argument("--model", default=MODEL, help="the faster-whisper model on Hugging Face to download")


def run(args: argparse.Namespace) -> None:
    """Download the model into the configured folder (voice.model_dir)."""
    # Hugging Face's newer "Xet" transfer stalled at 0 bytes on a slow network (29 Oct); plain HTTPS resumes and works
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:
        raise GroundTruthError("the speech library is not installed; run `uv sync --extra voice` first") from exc
    target = get_settings().voice.model_dir
    target.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s into %s ...", args.model, target)
    download_model(args.model, output_dir=str(target))
    logger.info("Done. Voice input is now available in `python main.py serve`.")

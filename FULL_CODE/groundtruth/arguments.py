"""Command-line options shared by several commands, with defaults taken from the settings."""

from __future__ import annotations

import argparse

from .config import get_settings


def add_data_arguments(ap: argparse.ArgumentParser) -> None:
    """--catalogue and --policy, defaulting to the configured files."""
    paths = get_settings().paths
    ap.add_argument("--catalogue", default=str(paths.catalogue), help="catalogue file (.jsonl, .json or .csv)")
    ap.add_argument("--policy", default=str(paths.policy), help="policy text file")


def add_model_arguments(ap: argparse.ArgumentParser) -> None:
    """--embed-model and --llm-model, defaulting to the configured model folders (none unless set)."""
    models = get_settings().models
    ap.add_argument(
        "--embed-model",
        default=models.embed_model,
        help="path to the provided embedding model (needs the calibrated semantic_config.json)",
    )
    ap.add_argument(
        "--llm-model",
        default=models.llm_model,
        help="path to the provided instruct model (last-resort query normaliser)",
    )

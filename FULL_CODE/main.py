"""GroundTruth: the one entry point.

    python main.py chat       interactive command-line chat
    python main.py serve      web demo at http://127.0.0.1:8000 (retailer view at /insights)
    python main.py evaluate   score a question set; reports go to output/
    python main.py submit     write submission/R3_predictions.jsonl for the pack's test questions
    python main.py score      score the dev questions with the pack's official scorer (needs `uv sync`)
    python main.py report     per-query latency and token-cost report over the pack's questions
    python main.py package    build teamID_R3.zip from a fresh clone, after the submission checklist
    python main.py bench      throughput and latency benchmark
    python main.py calibrate  pick the embedding-fallback thresholds with the provided model
    python main.py generate   write the synthetic catalogues and question sets into data/

Run `python main.py <command> --help` for a command's options.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from groundtruth.config import ConfigError, get_settings
from groundtruth.console import configure_console
from groundtruth.errors import GroundTruthError
from groundtruth.paths import ROOT

# Generators in the order their inputs need them (the scale and question sets read the catalogues written before).
GENERATORS = ("synthetic", "hard", "scale", "independent", "heldout", "heldout2")


def _generate(args: argparse.Namespace) -> None:
    names = GENERATORS if args.which == "all" else (args.which,)
    for name in names:
        # each generator runs in its own process: the question-set generators share module-level state
        subprocess.run([sys.executable, "-m", f"tools.generate_{name}"], check=True, cwd=ROOT)


def _add_generate_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "which", nargs="?", default="all", choices=("all", *GENERATORS), help="which data set to write (default: all)"
    )


def build_parser() -> argparse.ArgumentParser:
    # imported here, after the settings have loaded: these modules read settings at import time
    """The command-line parser, one sub-command per task."""
    from api import server
    from groundtruth import cli
    from groundtruth.evaluation import cost_report, official, scoring, submission
    from tools import bench, calibrate_semantic, make_submission_zip

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, add_arguments, handler, help_text in (
        ("chat", cli.add_arguments, cli.run, "interactive command-line chat"),
        ("serve", server.add_arguments, server.run, "run the web demo"),
        ("evaluate", scoring.add_arguments, scoring.run, "score a question set"),
        ("submit", submission.add_arguments, submission.run, "write predictions in the pack's submission format"),
        ("score", official.add_arguments, official.run, "score dev predictions with the pack's official scorer"),
        ("report", cost_report.add_arguments, cost_report.run, "per-query latency and token-cost report"),
        ("package", make_submission_zip.add_arguments, make_submission_zip.run, "build the checked submission zip"),
        ("bench", bench.add_arguments, bench.run, "throughput and latency benchmark"),
        ("calibrate", calibrate_semantic.add_arguments, calibrate_semantic.run, "calibrate the embedding fallback"),
        ("generate", _add_generate_arguments, _generate, "write synthetic data and question sets"),
    ):
        sub = commands.add_parser(name, help=help_text)
        add_arguments(sub)
        sub.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command; returns the exit code."""
    try:
        settings = get_settings()
    except ConfigError as exc:
        logging.basicConfig(format="%(levelname)s: %(message)s")
        logging.getLogger("groundtruth").error("invalid configuration: %s", exc)
        return 2
    configure_console(settings.logging.level)
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except GroundTruthError as exc:  # the user can fix these; the message says how
        logging.getLogger("groundtruth").error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

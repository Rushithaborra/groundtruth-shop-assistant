"""Score dev predictions with the organisers' own scorer (the pack's evaluate.py), optionally beside their baseline.

The pack's scripts are run as separate processes, unchanged and from the git-ignored data/pack/ folder, so our score
is exactly the one the organisers compute. They need pandas and numpy, which are development dependencies only
(`uv sync` installs them); the assistant itself does not use them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path

from ..arguments import add_model_arguments
from ..builder import build_assistant
from ..config import get_settings
from ..console import write_out
from ..errors import GroundTruthError
from .questions import load_questions
from .submission import predict, validate_predictions, write_predictions

logger = logging.getLogger(__name__)

FORMAT_ERROR_EXIT = 2  # the official scorer's exit code for a malformed submission


class ScorerError(GroundTruthError, RuntimeError):
    """The official scorer could not run, timed out, or rejected the submission."""


def _require_scorer_dependencies() -> None:
    missing = [name for name in ("pandas", "numpy") if importlib.util.find_spec(name) is None]
    if missing:
        raise ScorerError(
            f"the official scorer needs {' and '.join(missing)}; run `uv sync` and then `uv run python main.py score`"
        )


def _run_pack_script(args: list[str], timeout_s: float) -> str:
    """Run one of the pack's scripts in isolated mode (-I: no user site-packages or PYTHON* variables)."""
    cmd = [sys.executable, "-I", *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ScorerError(f"{Path(args[0]).name} did not finish within {timeout_s:g} s") from exc
    if done.returncode == FORMAT_ERROR_EXIT:
        raise ScorerError(f"the official scorer rejected the file:\n{done.stderr.strip()}")
    if done.returncode != 0:
        raise ScorerError(f"{Path(args[0]).name} failed (exit {done.returncode}):\n{done.stderr.strip()[-2000:]}")
    return done.stdout


def part_truth(part: str, out_dir: Path) -> Path:
    """The dev truth file, or a copy holding only one half of the dev split (see tools/split_dev.py)."""
    paths = get_settings().paths
    if part == "all":
        return paths.dev_truth
    keep = set(json.loads(paths.dev_split.read_text(encoding="utf-8"))[part])
    lines = [ln for ln in paths.dev_truth.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [ln for ln in lines if json.loads(ln)["question_id"] in keep]
    out = out_dir / f"dev_truth_{part}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(row + "\n" for row in rows), encoding="utf-8")
    return out


def score_file(predictions: Path, report: Path, details: bool = False, truth: Path | None = None) -> str:
    """Score a dev predictions file with the official scorer; returns its printed summary."""
    paths, evaluation = get_settings().paths, get_settings().evaluation
    args = [
        str(paths.official_scorer),
        "--submission", str(predictions),
        "--truth", str(truth or paths.dev_truth),
        "--out", str(report),
        "--data-dir", str(paths.dev_questions.parent),
    ]  # fmt: skip
    return _run_pack_script(args + (["--details"] if details else []), evaluation.scorer_timeout_s)


def add_arguments(ap: argparse.ArgumentParser) -> None:
    paths = get_settings().paths
    ap.add_argument("--catalogue", default=str(paths.pack_catalogue), help="the pack's catalogue")
    ap.add_argument("--policy", default=str(paths.pack_policy), help="the pack's policy")
    ap.add_argument("--baseline", action="store_true", help="also run and score the pack's shipped baseline")
    ap.add_argument("--details", action="store_true", help="also write per-question results (score_details.jsonl)")
    ap.add_argument(
        "--part",
        choices=("all", "tune", "check"),
        default="all",
        help="score all 40 dev questions, or one half of the dev split (tune on 'tune' only)",
    )
    add_model_arguments(ap)


def run(args: argparse.Namespace) -> None:
    _require_scorer_dependencies()
    paths, timeout_s = get_settings().paths, get_settings().evaluation.scorer_timeout_s
    reports = paths.reports_dir
    questions = load_questions(paths.dev_questions)
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model)
    rows = predict(assistant, questions)
    validate_predictions(rows, questions)
    ours = write_predictions(rows, paths.dev_predictions)
    truth = part_truth(args.part, reports)
    if args.baseline:
        baseline = reports / "baseline_dev_predictions.jsonl"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        script = paths.official_scorer.parent / "baseline" / "baseline.py"
        _run_pack_script([str(script), "--questions", str(paths.dev_questions), "--out", str(baseline)], timeout_s)
        write_out("=== The pack's shipped baseline ===")
        write_out(score_file(baseline, reports / f"official_baseline_dev_{args.part}.json", truth=truth))
        write_out("=== GroundTruth ===")
    write_out(score_file(ours, reports / f"official_dev_{args.part}.json", details=args.details, truth=truth))

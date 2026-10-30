"""Build teamID_psID.zip from a fresh git clone and check it against the guidelines' page-4 checklist first.

    python main.py package            # -> output/TEAM-000802_R3.zip (team and problem ID from config.json)

Only committed work is packaged (a clone of HEAD), so the zip never holds .env, data, models or local files. Every
check must pass before the zip is written; the failures are listed otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from groundtruth.config import get_settings
from groundtruth.console import write_out
from groundtruth.errors import GroundTruthError
from groundtruth.evaluation.questions import load_questions
from groundtruth.evaluation.submission import validate_predictions
from groundtruth.paths import ROOT

# Top level of the zipped project (guidelines, rule 2): README, .gitignore, submission/, FULL_CODE/, optional LICENSE.
# .git comes with a fresh clone; the line-ending rules (.gitattributes) live in FULL_CODE/ so nothing else is on top.
ALLOWED_TOP = {"README.md", ".gitignore", "LICENSE", "submission", "FULL_CODE", ".git"}
FORBIDDEN_NAMES = {".env", ".venv", "venv", "node_modules", "__pycache__", ".DS_Store", ".pytest_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = {".zip", ".db", ".sqlite", ".gguf", ".safetensors", ".pt", ".bin", ".pkl"}
FORBIDDEN_DIRS = ("FULL_CODE/data/pack", "FULL_CODE/models", "FULL_CODE/output", "FULL_CODE/data/scale")
README_SECTIONS = (
    "What it does",
    "How to run",
    "How to run the tests",
    "Architecture",
    "AI usage",
    "Pre-built and third-party code",
    "Limitations",
)
REQUIRED_FILES = ("FULL_CODE/main.py", "FULL_CODE/uv.lock", "FULL_CODE/.env.example", "FULL_CODE/pyproject.toml")


class PackagingError(GroundTruthError, RuntimeError):
    """The project can't be packaged: the clone failed or a checklist item failed."""


def check_project(root: Path, test_questions: Path | None = None) -> list[str]:
    """Problems found in a project folder (empty when it passes); `test_questions` enables the prediction check."""
    problems = [f"unexpected top-level item: {p.name}" for p in sorted(root.iterdir()) if p.name not in ALLOWED_TOP]
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            problems.append(f"must not be in the zip: {rel}")
        elif any(rel == d or rel.startswith(d + "/") for d in FORBIDDEN_DIRS):
            problems.append(f"pack data, models or output must not be in the zip: {rel}")
    problems += [f"missing {f}" for f in ("README.md", *REQUIRED_FILES) if not (root / f).is_file()]
    readme = root / "README.md"
    if readme.is_file():
        headings = set(re.findall(r"^##\s+(.+?)\s*$", readme.read_text(encoding="utf-8"), re.MULTILINE))
        problems += [f"README is missing the section '{s}'" for s in README_SECTIONS if s not in headings]
    problems += _check_predictions(root / "submission" / "R3_predictions.jsonl", test_questions)
    return problems


def _check_predictions(path: Path, test_questions: Path | None) -> list[str]:
    """Problems with the submission file (missing, unreadable or invalid); empty when it is fine."""
    if not path.is_file():
        return ["missing submission/R3_predictions.jsonl"]
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if test_questions is not None and test_questions.is_file():
            validate_predictions(rows, load_questions(test_questions))
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"submission/R3_predictions.jsonl is not valid: {exc}"]
    return []


def zip_folder(folder: Path, zip_path: Path) -> Path:
    """Zip `folder` so the archive holds the folder itself (teamID_psID/...), as the guidelines show."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            zf.write(path, path.relative_to(folder.parent).as_posix())
    return zip_path


def _fresh_clone(repo: Path, dest: Path) -> None:
    """Clone the repository, so that only committed work is packaged."""
    try:
        subprocess.run(
            ["git", "clone", "--quiet", str(repo), str(dest)], check=True, capture_output=True, text=True, timeout=300
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise PackagingError(f"git clone of {repo} failed: {getattr(exc, 'stderr', '') or exc}") from exc


def _uncommitted(repo: Path) -> str:
    """Git's list of uncommitted changes (empty when everything is committed)."""
    done = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The zip builder's option: where to write the zip."""
    ap.add_argument("--out-dir", help="where to write the zip (default: output/)")


def run(args: argparse.Namespace) -> None:
    """Clone, check the project against the submission checklist and write the zip; stops if the checklist fails."""
    settings = get_settings()
    name = f"{settings.submission.team_id}_{settings.submission.problem_id}"
    repo = ROOT.parent
    if _uncommitted(repo):
        write_out("Note: there are uncommitted changes; only committed work is packaged.")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / name
        _fresh_clone(repo, folder)
        problems = check_project(folder, settings.paths.test_questions)
        if problems:
            raise PackagingError("checklist failed:\n  " + "\n  ".join(problems))
        out_dir = Path(args.out_dir) if args.out_dir else settings.paths.reports_dir.parent
        zip_path = zip_folder(folder, out_dir / f"{name}.zip")
    size_mb = zip_path.stat().st_size / 1e6
    write_out(f"checklist passed; wrote {zip_path} ({size_mb:.1f} MB). Upload this file.")

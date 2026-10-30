"""Developer tools: data and question-set generators, benchmark, embedding calibration."""

from __future__ import annotations

import subprocess
import sys

from groundtruth.errors import GroundTruthError
from groundtruth.paths import ROOT

GENERATOR_TIMEOUT_S = 600  # the largest set (5,000 products and its questions) takes a few seconds; this is a stop


def run_generator(name: str, quiet: bool = False) -> None:
    """Run tools.generate_<name> in its own process: the question-set generators share module-level state."""
    cmd = [sys.executable, "-m", f"tools.generate_{name}"]
    try:
        subprocess.run(
            cmd, check=True, cwd=ROOT, timeout=GENERATOR_TIMEOUT_S, stdout=subprocess.DEVNULL if quiet else None
        )
    except subprocess.TimeoutExpired as exc:
        raise GroundTruthError(f"generating the {name} data took over {GENERATOR_TIMEOUT_S} s and was stopped") from exc
    except subprocess.CalledProcessError as exc:
        raise GroundTruthError(
            f"generating the {name} data failed (exit {exc.returncode}); see the error above"
        ) from exc

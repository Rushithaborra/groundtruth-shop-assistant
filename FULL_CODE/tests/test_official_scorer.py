"""Running the organisers' scorer: failures surface as ScorerError with a clear message (subprocess mocked)."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from groundtruth.evaluation import official


def finished(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class OfficialScorerTests(unittest.TestCase):
    def test_success_returns_the_scorer_summary_and_runs_it_isolated_with_a_timeout(self):
        with mock.patch.object(official.subprocess, "run", return_value=finished(0, "PRIMARY SCORE : 0.7")) as run:
            out = official.score_file(Path("pred.jsonl"), Path("report.json"), details=True)
        self.assertIn("PRIMARY SCORE", out)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], [sys.executable, "-I"])
        self.assertIn("--details", cmd)
        self.assertEqual(cmd[cmd.index("--submission") + 1], "pred.jsonl")
        self.assertGreater(run.call_args.kwargs["timeout"], 0)

    def test_format_error_exit_code_is_reported_with_the_scorer_message(self):
        result = finished(official.FORMAT_ERROR_EXIT, stderr="FORMAT ERROR\n  line 3: action")
        with (
            mock.patch.object(official.subprocess, "run", return_value=result),
            self.assertRaisesRegex(official.ScorerError, "rejected the file:\nFORMAT ERROR"),
        ):
            official.score_file(Path("p.jsonl"), Path("r.json"))

    def test_timeout_and_crash_become_scorer_errors(self):
        timeout = subprocess.TimeoutExpired(cmd="evaluate.py", timeout=1)
        with (
            mock.patch.object(official.subprocess, "run", side_effect=timeout),
            self.assertRaisesRegex(official.ScorerError, "did not finish"),
        ):
            official.score_file(Path("p.jsonl"), Path("r.json"))
        with (
            mock.patch.object(official.subprocess, "run", return_value=finished(1, stderr="Traceback ...")),
            self.assertRaisesRegex(official.ScorerError, r"failed \(exit 1\)"),
        ):
            official.score_file(Path("p.jsonl"), Path("r.json"))

    def test_missing_pandas_is_explained_before_anything_runs(self):
        with (
            mock.patch.object(official.importlib.util, "find_spec", return_value=None),
            self.assertRaisesRegex(official.ScorerError, "uv sync"),
        ):
            official._require_scorer_dependencies()


if __name__ == "__main__":
    unittest.main()

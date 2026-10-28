"""The latency and token-cost report: one row per question, honest totals, and a clearly labelled LLM estimate."""

import csv
import tempfile
import unittest
from pathlib import Path

from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.evaluation.cost_report import FIELDS, _percentile, measure, summarise, write_report
from groundtruth.evaluation.questions import Question
from groundtruth.paths import ROOT

QUESTIONS = {
    "dev": [Question("D1", "C1", 1, "running shoes"), Question("D2", "C1", 2, "under 2500")],
    "test": [Question("T1", "C2", 1, "do you sell iPhones?"), Question("T2", "C3", 1, "return policy")],
}


class CostReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assistant = build_assistant(
            ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt", measure_tokens=True
        )
        cls.rows = measure(assistant, QUESTIONS)
        cls.summary = summarise(cls.rows)

    def test_one_row_per_question_with_every_column(self):
        self.assertEqual([r["question_id"] for r in self.rows], ["D1", "D2", "T1", "T2"])
        self.assertEqual([r["split"] for r in self.rows], ["dev", "dev", "test", "test"])
        for row in self.rows:
            self.assertEqual(set(row), set(FIELDS))
            self.assertGreaterEqual(row["latency_ms"], 0)

    def test_the_rule_based_assistant_uses_no_model_tokens(self):
        self.assertEqual(self.summary["path_share"]["rules"], 1.0)
        self.assertEqual(self.summary["model_tokens_measured"], {"in": 0, "out": 0, "per_query": 0.0})
        self.assertGreater(self.summary["context_tokens_estimated"]["mean"], 0)

    def test_llm_estimate_is_labelled_and_scales_with_the_number_of_queries(self):
        cost = get_settings().cost_report
        estimate = self.summary["llm_every_query_ESTIMATE"]
        self.assertGreaterEqual(estimate["tokens_in"], cost.llm_prompt_tokens * 4)
        self.assertEqual(estimate["tokens_out"], cost.llm_output_tokens * 4)
        self.assertAlmostEqual(estimate["seconds"], cost.llm_seconds_per_call * 4, places=1)
        self.assertIn("estimate", estimate["note"])

    def test_percentiles_on_known_values(self):
        values = [float(v) for v in range(1, 101)]
        self.assertEqual((_percentile(values, 0.5), _percentile(values, 0.95)), (50.0, 95.0))
        self.assertEqual(_percentile([7.0], 0.95), 7.0)

    def test_report_files_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            per_query, totals = write_report(self.rows, self.summary, Path(tmp))
            with per_query.open(encoding="utf-8") as fh:
                read = list(csv.DictReader(fh))
            self.assertEqual(len(read), 4)
            self.assertEqual(tuple(read[0]), FIELDS)
            self.assertIn('"questions": 4', totals.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

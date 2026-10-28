"""Replies must not depend on Python's per-process hash seed: the same question gives the same reply on every run."""

import unittest

from groundtruth.answering import ANSWER
from groundtruth.builder import build_assistant
from groundtruth.paths import ROOT


class DeterminismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        cls.first, cls.second = cls.a.index.products["P0082"], cls.a.index.products["P0083"]

    def test_compare_lists_products_in_the_order_the_shopper_named_them(self):
        forward = self.a.ask(f"which is cheaper, {self.first.title} or {self.second.title}?")
        backward = self.a.ask(f"which is cheaper, {self.second.title} or {self.first.title}?")
        self.assertEqual(forward.action, ANSWER)
        self.assertEqual(forward.citations, [self.first.id, self.second.id])
        self.assertEqual(backward.citations, [self.second.id, self.first.id])


if __name__ == "__main__":
    unittest.main()

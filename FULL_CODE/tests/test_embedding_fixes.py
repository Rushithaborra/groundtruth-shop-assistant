"""Embedding fallback: the target words it may map onto, and what happens when the model fails or its cache is bad.

The real embedding model is not needed: these check the plumbing around it, with fake embedders.

Run: python -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path

from test_fallbacks import FakeEmbedder

from groundtruth.answering import REFUSE, ShoppingAssistant
from groundtruth.builder import build_assistant
from groundtruth.catalogue import CatalogueIndex, load_catalogue
from groundtruth.models.calls import ModelCallError
from groundtruth.models.semantic import SemanticFallback, embedding_targets, type_words
from groundtruth.paths import ROOT
from groundtruth.policy import load_policy

SUB_CATEGORY = {  # last word of a title -> the pack's sub_category for it
    "Shoes": "Running Shoes",
    "Earbuds": "Earbuds",
    "Bank": "Power Banks",
    "Backpack": "Backpacks",
    "Jeans": "Jeans",
    "Kurta": "Kurtas",
    "Smartwatch": "Smartwatches",
    "T-Shirt": "T-Shirts",
    "Shirt": "Shirts",
    "Hoodie": "Hoodies",
    "Sandals": "Sandals",
    "Bedsheet": "Bedsheets",
    "Bottle": "Water Bottles",
    "Kettle": "Electric Kettles",
    "Wallet": "Wallets",
    "Speaker": "Speakers",
}


def pack_shaped_catalogue(folder: str) -> Path:
    """The bundled catalogue with a sub_category on every product, as the organisers' pack has."""
    rows = [json.loads(line) for line in (ROOT / "data" / "catalogue.jsonl").read_text().splitlines() if line.strip()]
    for row in rows:
        row["attributes"]["sub_category"] = SUB_CATEGORY[row["title"].split("(")[0].split()[-1]]
    path = Path(folder) / "catalogue.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


class TargetWordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pack_index = CatalogueIndex(load_catalogue(pack_shaped_catalogue(cls._tmp.name)))
        cls.plain_index = CatalogueIndex(load_catalogue(ROOT / "data" / "catalogue.jsonl"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_kind_words_stay_targets_when_the_catalogue_has_sub_categories(self):
        words = set(type_words(self.pack_index))
        self.assertLessEqual({"shoe", "earbud", "kurta", "jean", "bank", "wallet"}, words)

    def test_sub_categories_lose_no_target(self):
        self.assertLessEqual(set(embedding_targets(self.plain_index)), set(embedding_targets(self.pack_index)))

    def test_targets_name_products_not_categories_or_describing_words(self):
        targets = set(embedding_targets(self.pack_index))
        self.assertFalse(targets & {"apparel", "home", "electronic", "wireless", "bluetooth", "electric", "water"})
        self.assertIn("shoe", targets)

    def test_a_catalogue_without_title_nouns_keeps_the_broader_list(self):
        self.assertEqual(embedding_targets(CatalogueIndex([])), [])


class FailingEmbedder(FakeEmbedder):
    """Works until told to fail, the way a model that times out or is busy with another call does."""

    failing = False

    def encode(self, texts):
        if self.failing:
            raise ModelCallError("embedding timed out")
        return super().encode(texts)


class ModelFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = CatalogueIndex(load_catalogue(ROOT / "data" / "catalogue.jsonl"))
        cls.policy = load_policy(ROOT / "data" / "policy.txt")

    def build(self):
        embedder = FailingEmbedder({w: i for i, w in enumerate(embedding_targets(self.index))}, {})
        fallback = SemanticFallback(self.index, self.policy, embedder, 0.8, 0.5, 0.7)
        return embedder, fallback, ShoppingAssistant(self.index, self.policy, semantic=fallback)

    def test_a_failed_model_call_gives_the_rule_based_refusal(self):
        embedder, _, assistant = self.build()
        embedder.failing = True
        self.assertEqual(assistant.ask("flurbos under 3000").action, REFUSE)

    def test_a_failed_model_call_on_a_policy_question_gives_the_rule_based_reply(self):
        embedder, _, assistant = self.build()
        embedder.failing = True
        self.assertEqual(assistant.ask("money back timeline").action, REFUSE)

    def test_a_failure_is_not_remembered_as_a_result(self):
        embedder, fallback, _ = self.build()
        embedder.failing = True
        self.assertIsNone(fallback.nearest_type("flurbo"))
        embedder.failing = False
        self.assertIsNotNone(fallback.nearest_type("flurbo"))


class StartUpTests(unittest.TestCase):
    def test_a_missing_embedding_model_leaves_the_rule_based_assistant_running(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "semantic_config.json"
            config.write_text(json.dumps({"map_threshold": 0.8, "alt_threshold": 0.6, "policy_threshold": 0.5}))
            with self.assertLogs("groundtruth.builder", "WARNING"):
                assistant = build_assistant(
                    ROOT / "data" / "catalogue.jsonl",
                    ROOT / "data" / "policy.txt",
                    embed_model=str(Path(folder) / "no-such-model"),
                    semantic_config=config,
                    cache_dir=folder,
                )
        self.assertIsNone(assistant.semantic)
        self.assertEqual(assistant.ask("running shoes under 3000").path, "rules")


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = CatalogueIndex(load_catalogue(ROOT / "data" / "catalogue.jsonl"))
        cls.policy = load_policy(ROOT / "data" / "policy.txt")

    def build(self, folder):
        embedder = FakeEmbedder({w: i for i, w in enumerate(embedding_targets(self.index))}, {})
        return embedder, SemanticFallback(self.index, self.policy, embedder, 0.8, cache_dir=folder)

    def cache_file(self, folder):
        (path,) = Path(folder).glob("embeddings-*.json")
        return path

    def test_second_start_reads_the_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build(folder)
            embedder, _ = self.build(folder)
            self.assertEqual(embedder.calls, 0)

    def test_a_cut_off_cache_file_is_rebuilt_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build(folder)
            path = self.cache_file(folder)
            path.write_text(path.read_text()[:100])
            embedder, fallback = self.build(folder)
            self.assertEqual(embedder.calls, 1)
            self.assertEqual(len(fallback.target_vecs), len(fallback.targets))
            json.loads(path.read_text())  # and the file is whole again

    def test_a_cache_of_the_wrong_size_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build(folder)
            path = self.cache_file(folder)
            path.write_text(json.dumps(json.loads(path.read_text())[:-1]))
            embedder, fallback = self.build(folder)
            self.assertEqual(embedder.calls, 1)
            self.assertEqual(len(fallback.policy_vecs), len(fallback.policy_units))

    def test_no_half_written_file_is_left_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build(folder)
            self.assertEqual([p.suffix for p in Path(folder).iterdir()], [".json"])


if __name__ == "__main__":
    unittest.main()

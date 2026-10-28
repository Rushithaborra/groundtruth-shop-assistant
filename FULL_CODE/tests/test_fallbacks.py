"""Embedding fallback and LLM normaliser, tested with fake models (the real ones are only in the hackathon setup).

These check the plumbing and the safety rules: fallbacks run only on misses, can only change how the query is
understood, never what the reply asserts, and anything the model invents is dropped.

Run: python -m unittest discover -s tests
"""

import json
import unittest

from groundtruth.answering import ANSWER, REFUSE, ShoppingAssistant
from groundtruth.catalogue import CatalogueIndex, load_catalogue
from groundtruth.grounding import Grounding
from groundtruth.models.calls import ModelCallError
from groundtruth.models.normaliser import LLMNormaliser
from groundtruth.models.semantic import SemanticFallback, type_words
from groundtruth.paths import ROOT
from groundtruth.policy import load_policy
from tools.calibrate_semantic import calibrate


class FakeEmbedder:
    """Each catalogue type word gets its own axis; listed shopper words lean towards one axis by a set amount."""

    name = "fake"

    def __init__(self, axes, leaning, policy_alias=None):
        self.axes, self.leaning, self.policy_alias = axes, leaning, policy_alias or {}
        self.dim = len(axes) + 4096
        self.own = {}
        self.calls = 0

    def _own_axis(self, text):
        if text not in self.own:
            self.own[text] = len(self.axes) + len(self.own)
        return self.own[text]

    def encode(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            t = self.policy_alias.get(t, t)
            v = [0.0] * self.dim
            if t in self.axes:
                v[self.axes[t]] = 1.0
            elif t in self.leaning:
                target, sim = self.leaning[t]
                v[self.axes[target]] = sim
                v[self._own_axis(t)] = (1 - sim * sim) ** 0.5
            else:
                v[self._own_axis(t)] = 1.0
            out.append(v)
        return out


class FallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = CatalogueIndex(load_catalogue(ROOT / "data" / "catalogue.jsonl"))
        cls.policy = load_policy(ROOT / "data" / "policy.txt")
        refund = next(s for sec in cls.policy for s, _ in sec.sentences if "Refunds" in s)
        words = type_words(cls.index) + [refund]
        cls.axes = {w: i for i, w in enumerate(words)}
        cls.leaning = {
            "zoomer": ("running", 0.9),
            "gizmo": ("earbud", 0.6),
            "nike": ("running", 0.99),
            "money back timeline": (refund, 0.8),
        }

    def assistant(self, embedder=None, normaliser=None):
        sem = None
        if embedder:
            sem = SemanticFallback(
                self.index, self.policy, embedder, map_threshold=0.8, alt_threshold=0.5, policy_threshold=0.7
            )
        a = ShoppingAssistant(self.index, self.policy, semantic=sem, normaliser=normaliser)
        return a, Grounding(a.index, a.sections)

    def grounded(self, g, r, q):
        self.assertFalse(g.check(r.text, q)["problems"], r.text)

    # -- embedding fallback ----------------------------------------------------------------------------
    def test_word_above_map_threshold_is_searched_as_catalogue_word(self):
        a, g = self.assistant(FakeEmbedder(self.axes, self.leaning))
        r = a.ask("zoomers under 3000")
        self.assertEqual((r.action, r.path), (ANSWER, "semantic"))
        self.assertIn('"zoomer" as "running"', r.text)
        self.assertTrue(all("Running" in a.index.products[c].title for c in r.citations))
        self.grounded(g, r, "zoomers under 3000")

    def test_word_between_thresholds_refuses_with_alternative(self):
        a, g = self.assistant(FakeEmbedder(self.axes, self.leaning))
        r = a.ask("gizmo")
        self.assertEqual(r.action, REFUSE)
        self.assertIn("closest products", r.text)
        self.assertTrue(r.citations and all("Earbuds" in a.index.products[c].title for c in r.citations))
        self.grounded(g, r, "gizmo")

    def test_unrelated_word_still_refused(self):
        a, _ = self.assistant(FakeEmbedder(self.axes, self.leaning))
        r = a.ask("flurbos")
        self.assertEqual(r.action, REFUSE)
        self.assertFalse(r.citations)

    def test_external_brand_never_mapped_even_when_similar(self):
        a, _ = self.assistant(FakeEmbedder(self.axes, self.leaning))
        self.assertEqual(a.ask("nike").action, REFUSE)

    def test_fast_path_hits_do_not_call_the_model(self):
        emb = FakeEmbedder(self.axes, self.leaning)
        a, _ = self.assistant(emb)
        before = emb.calls
        r = a.ask("running shoes under 3000")
        self.assertEqual((r.path, emb.calls), ("rules", before))

    def test_policy_sentence_retrieval(self):
        a, g = self.assistant(FakeEmbedder(self.axes, self.leaning))
        r = a.ask("money back timeline")
        self.assertEqual((r.action, r.path), (ANSWER, "semantic"))
        self.assertIn("[policy:returns]", r.text)
        self.grounded(g, r, "money back timeline")

    def test_calibration_sets_threshold_above_every_unsold_word(self):
        fb = SemanticFallback(self.index, self.policy, FakeEmbedder(self.axes, {"iphone": ("power", 0.7)}), 1.0)
        cfg = calibrate(fb)["config"]
        self.assertGreater(cfg["map_threshold"], 0.7)

    # -- LLM normaliser --------------------------------------------------------------------------------
    def fake_llm(self, payload, raises=False):
        calls = []

        def generate(system, user):
            calls.append(user)
            if raises:  # what the real generator raises once call_model gives up (timeout or repeated errors)
                raise ModelCallError("instruct model failed after 2 attempts")
            return (payload if isinstance(payload, str) else json.dumps(payload)), 321, 45

        return LLMNormaliser(generate), calls

    def test_llm_type_is_used_and_tokens_reported(self):
        norm, calls = self.fake_llm({"product_type": "running", "filters": {}, "price_max": 3000})
        a, g = self.assistant(normaliser=norm)
        r = a.ask("flurbos for my morning run under 3000")
        self.assertEqual((r.action, r.path, r.llm_input_tokens, r.llm_output_tokens), (ANSWER, "llm", 321, 45))
        self.assertTrue(all(a.index.products[c].price <= 3000 for c in r.citations))
        self.grounded(g, r, "flurbos for my morning run under 3000")

    def test_llm_inventions_are_dropped(self):
        norm, _ = self.fake_llm(
            {"product_type": "spaceship", "filters": {"color": "ultraviolet", "brand": "Nike"}, "price_max": 999}
        )
        a, _ = self.assistant(normaliser=norm)
        r = a.ask("flurbos under 3000")  # 999 is not the shopper's number; spaceship isn't sold
        self.assertEqual(r.action, REFUSE)

    def test_llm_flagging_out_of_catalogue_keeps_refusal(self):
        norm, _ = self.fake_llm({"product_type": "earbud", "out_of_catalogue": ["flurbo"]})
        a, _ = self.assistant(normaliser=norm)
        self.assertEqual(a.ask("flurbo").action, REFUSE)

    def test_llm_garbage_or_crash_falls_back_to_rules(self):
        for norm in (self.fake_llm("Sure! Here you go: not json")[0], self.fake_llm({}, raises=True)[0]):
            a, _ = self.assistant(normaliser=norm)
            self.assertEqual(a.ask("flurbos").action, REFUSE)

    def test_llm_not_called_on_fast_path(self):
        norm, calls = self.fake_llm({"product_type": "running"})
        a, _ = self.assistant(normaliser=norm)
        a.ask("running shoes")
        a.ask("return policy")
        self.assertEqual(calls, [])

    def test_llm_reply_text_never_reaches_the_shopper(self):
        norm, _ = self.fake_llm('{"product_type": "running"} The price is ₹1 and it is free!')
        a, g = self.assistant(normaliser=norm)
        r = a.ask("flurbos")
        self.assertNotIn("₹1 ", r.text + " ")
        self.grounded(g, r, "flurbos")


if __name__ == "__main__":
    unittest.main()

"""Shopper phrasings found by probing with new, realistic messages (round 3).

Run: python -m unittest discover -s tests
"""

import unittest

from groundtruth.answering import ANSWER, CLARIFY, REFUSE, Session
from groundtruth.builder import build_assistant
from groundtruth.grounding import Grounding
from groundtruth.paths import ROOT
from groundtruth.text import format_inr


class ShopperPhrasingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        cls.g = Grounding(cls.a.index, cls.a.sections)
        cls.products = cls.a.index.products

    def ask(self, *turns):
        s = Session()
        for t in turns:
            r = self.a.ask(t, s)
        self.assertFalse(self.g.check(r.text, " ".join(turns))["problems"], r.text)
        return r

    def cited(self, r):
        return [self.products[c] for c in r.citations if c in self.products]

    def test_school_bag_means_backpack(self):
        for q in ("school bag", "college bag", "laptop bag"):
            r = self.ask(q)
            self.assertEqual(r.action, ANSWER, q)
            self.assertTrue(all("Backpack" in p.title for p in self.cited(r)), q)

    def test_hinglish_best_cheapest_costliest(self):
        r = self.ask("sabse acchi rating wali smartwatch")
        self.assertEqual(r.action, ANSWER)
        best = max((p.rating or 0) for p in self.products.values() if "Smartwatch" in p.title)
        self.assertEqual(self.cited(r)[0].rating, best)
        r = self.ask("sabse sasta earbuds")
        prices = [p.price for p in self.cited(r)]
        self.assertEqual(prices, sorted(prices))
        # a superlative ("sabse" = the most) names one product, with its price, not a list
        r = self.ask("sabse mehenga kurta")
        self.assertEqual(r.action, ANSWER)
        [pick] = self.cited(r)
        self.assertEqual(pick.price, max(p.price for p in self.products.values() if "Kurta" in p.title))
        self.assertIn(format_inr(pick.price), r.text)
        self.assertEqual(r.claims.prices, [(pick.id, pick.price)])

    def test_exclusion_follow_up_keeps_the_product(self):
        r = self.ask("earbuds", "not black")
        self.assertEqual(r.action, ANSWER)
        for p in self.cited(r):
            self.assertIn("Earbuds", p.title)
            self.assertNotIn("black", [v.lower() for v in p.attr_values("color")])

    def test_excluding_a_brand_the_shop_lacks_is_not_a_refusal(self):
        self.assertEqual(self.ask("running shoes", "not nike").action, ANSWER)
        self.assertEqual(self.ask("running shoes not adidas").action, ANSWER)
        self.assertEqual(self.ask("not nike").action, CLARIFY)  # says only what isn't wanted
        self.assertEqual(self.ask("nike shoes").action, REFUSE)

    def test_noise_cancellation_is_a_feature_not_a_brand_or_policy(self):
        r = self.ask("earbuds with noise cancellation")
        self.assertEqual(r.action, ANSWER)
        self.assertNotIn("policy", r.text)
        self.assertTrue(all("Earbuds" in p.title for p in self.cited(r)))

    def test_size_and_attribute_questions_about_one_product(self):
        pid = next(p.id for p in self.products.values() if p.attr_values("size"))
        sizes = ", ".join(self.products[pid].attr_values("size"))
        for q in (f"{pid} ka size kya hai", f"what sizes does {pid} come in", f"{pid} size"):
            r = self.ask(q)
            self.assertEqual(r.action, ANSWER, q)
            self.assertIn(sizes, r.text, q)
        colour = self.products[pid].attr_values("color")[0]
        self.assertIn(colour, self.ask(f"colour of {pid}").text)

    def test_sizes_is_not_read_as_size_s(self):
        from groundtruth.understanding import understand

        self.assertIsNone(understand("what sizes does it come in", self.a.index).spec.size)

    def test_filter_on_an_attribute_the_type_lacks_is_dropped_with_a_note(self):
        typed = {}
        for p in self.products.values():
            typed.setdefault(p.title.split("(")[0].split()[-1], []).append(p)
        kind = next((k for k, ps in typed.items() if not any(p.attr_values("gender") for p in ps)), None)
        if kind is None:
            self.skipTest("every product type lists a gender in this catalogue")
        r = self.ask(f"{kind.lower()} for women")
        self.assertEqual(r.action, ANSWER)
        self.assertIn("doesn't list gender", r.text)

    def test_orders_accounts_and_offers_get_one_clear_refusal(self):
        for q in (
            "track my order",
            "mere order ka status",
            "apply coupon",
            "is there any discount",
            "do you have gift cards",
            "can i pay with paytm",
            "do you have a store in hyderabad",
        ):
            r = self.ask(q)
            self.assertEqual(r.action, REFUSE, q)
            self.assertTrue(r.text.startswith("I can't see orders"), q)
        self.assertIn("[policy:cancellations]", self.ask("order cancel kaise kare").text)  # policy still wins

    def test_small_talk(self):
        for q in ("thanks", "thank you so much", "ok thanks"):
            self.assertTrue(self.ask(q).text.startswith("You're welcome"), q)
        for q in ("what can you do", "help"):
            self.assertTrue(self.ask(q).text.startswith("Hi! I can help"), q)

    def test_cheapest_overall(self):
        r = self.ask("cheapest thing you have")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(self.cited(r)[0].price, min(p.price for p in self.products.values()))

    def test_a_describing_word_alone_does_not_make_an_unsold_product_answerable(self):
        for q in ("wireless mouse", "electric scooter"):
            self.assertEqual(self.ask(q).action, REFUSE, q)
        for q in ("wireless earbuds", "trainers for running", "sneakers for gym"):
            self.assertEqual(self.ask(q).action, ANSWER, q)

    def test_hindi_filler_words(self):
        r = self.ask("kya aapke paas lehenga hai")
        self.assertEqual(r.action, REFUSE)
        self.assertIn('"lehenga"', r.text)


if __name__ == "__main__":
    unittest.main()

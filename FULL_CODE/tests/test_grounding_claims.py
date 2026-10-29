"""The grounding checker also holds attribute words and policy wording to the sources: a colour, material or brand
that the cited record doesn't give, and a policy sentence that isn't the policy's own, are unsupported claims."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import Session
from groundtruth.builder import build_assistant
from groundtruth.grounding import Grounding


def product(pid, title, sub, price, attrs):
    return {
        "product_id": pid,
        "title": title,
        "brand": title.split()[0],
        "category": "Test",
        "sub_category": sub,
        "description": f"{title}.",
        "attributes": attrs,
        "price_inr": price,
        "mrp_inr": price,
        "stock_status": "in_stock",
        "rating": 4.0,
        "review_count": 10,
    }


CATALOGUE = [
    product("SKU-C", "Threadly Kora Cotton Shirt - Blue", "Shirts", 899, {"material": "cotton", "colour": "blue"}),
    product("SKU-S", "Threadly Sera Silk Shirt - Red", "Shirts", 2499, {"material": "silk", "colour": "red"}),
    product("SKU-L", "Lomax Vida Linen Shirt - Blue", "Shirts", 1299, {"material": "linen", "colour": "blue"}),
    # attribute values that repeat attribute names: "color", "water" (as in the pack: colour_type "color pencil")
    product(
        "SKU-K",
        "Kalaa Vivid Wax Crayons",
        "Crayons",
        199,
        {"colour_type": "color crayon", "care": "wipe with water", "water_resistance_rating": "IPX4"},
    ),
]
POLICY = (
    "# Policy\n\n## 1. Returns\n\n### 1.1 Return window\n"
    "Items can be returned within 30 days of delivery. Refunds go to the original payment method.\n\n"
    "## 2. Shipping\n\n### 2.1 Delivery time\nStandard shipping takes 3 to 5 business days across India.\n"
)
C, S = "Threadly Kora Cotton Shirt - Blue", "Threadly Sera Silk Shirt - Red"
RETURN_SENTENCE = "Items can be returned within 30 days of delivery."


class AttributeAndPolicyClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.assistant = build_assistant(cat, pol)
        cls.grounding = Grounding(cls.assistant.index, cls.assistant.sections)
        cls.sections = list(cls.assistant.sections)

    def problems(self, text, question=""):
        return self.grounding.check(text, question)["problems"]

    def test_a_wrong_material_for_the_cited_product_is_flagged(self):
        flagged = self.problems(f"The material of the {C} is silk [SKU-C].", "what is it made of")
        self.assertEqual(flagged, ["'silk' is not in the cited record"])
        self.assertEqual(self.problems(f"The material of the {C} is cotton [SKU-C]."), [])

    def test_a_wrong_colour_or_brand_is_flagged(self):
        self.assertEqual(self.problems(f"The color of the {C} is red [SKU-C]."), ["'red' is not in the cited record"])
        self.assertEqual(self.problems(f"The {C} is made by Lomax [SKU-C]."), ["'lomax' is not in the cited record"])

    def test_each_product_line_is_checked_against_its_own_citation(self):
        swapped = f"- {C}: silk [SKU-C]\n- {S}: cotton [SKU-S]"  # each product given the other's material
        self.assertEqual(len(self.problems(swapped)), 2)
        right = f"- {C} (cotton) [SKU-C]\n- {S} (silk) [SKU-S]"
        self.assertEqual(self.problems(right), [])

    def test_the_shoppers_own_words_may_be_repeated_but_not_asserted_without_a_source(self):
        refusal = (
            "No silk shirt under ₹100 in the catalogue; the cheapest I have is "
            "Threadly Sera Silk Shirt - Red at ₹2,499 [SKU-S]."
        )
        self.assertEqual(self.problems(refusal, "silk shirt under 100"), [])
        self.assertEqual(self.problems("It is made of silk."), ["'silk' is not in the cited record"])

    def test_attribute_names_are_not_claims(self):
        self.assertEqual(self.problems("There are more matches; tell me a brand, colour or budget."), [])
        missing = f"The catalogue does not list a water_resistance_rating for the {C} [SKU-C]."
        self.assertEqual(self.problems(missing), [])
        self.assertEqual(self.problems(f"The {C} is wiped with water [SKU-C]."), ["'water' is not in the cited record"])

    def test_an_invented_policy_sentence_is_flagged_and_the_policys_own_is_not(self):
        invented = "Returns are always free and need no packaging. [policy:1.1]"
        self.assertEqual(len(self.problems(invented)), 1)
        self.assertEqual(self.problems(f"{RETURN_SENTENCE} [policy:1.1]"), [])

    def test_a_policy_sentence_cited_to_the_wrong_section_is_flagged(self):
        found = self.problems(f"{RETURN_SENTENCE} [policy:2.1]")
        self.assertTrue(any("not quoted from [policy:2.1]" in problem for problem in found), found)

    def test_a_lead_in_before_a_quoted_policy_sentence_is_fine(self):
        self.assertEqual(self.problems(f"What it does say: {RETURN_SENTENCE} [policy:1.1]"), [])

    def test_the_assistants_own_replies_pass(self):
        for question in (
            f"price of {C}",
            f"what is the material of {C}",
            f"what colour is {S}",
            "blue shirt",
            "cotton shirt under 100",
            "silk shirt",
            "shirt not cotton",
            f"compare {C} and {S}",
            "return policy",
            "how many days to return",
            "delivery on sunday",
            "do you ship outside india",
            "refund kab milega",
        ):
            reply = self.assistant.ask(question, Session())
            self.assertEqual(self.problems(reply.text, question), [], f"{question}: {reply.text}")


if __name__ == "__main__":
    unittest.main()

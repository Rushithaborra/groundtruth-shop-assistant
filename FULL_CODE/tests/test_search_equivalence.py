"""The set-based search (postings, budget index, top-n selection) must equal a plain filter-and-sort on random specs."""

from __future__ import annotations

import random
import unittest

from groundtruth.catalogue import CatalogueIndex, Spec
from groundtruth.catalogue.index import GENDER_KEY, UNIT_WORDS
from tools import generate_scale


def reference(index: CatalogueIndex, spec: Spec, qt: set[str]) -> list[str]:
    """The original method: products from the postings, filtered one by one, then sorted by the full key."""
    pool = [index.products[i] for i in index._candidate_ids(spec, postings_only=True)]
    hits = [p for p in pool if index._matcher(spec, True)(p)]
    if not hits and spec.desc_tokens:
        hits = [p for p in pool if index._matcher(spec, False)(p)]
    return [p.id for p in sorted(hits, key=index._sort_key(spec, qt))]


class SearchEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        generate_scale.N_PRODUCTS = 3000
        cls.index = CatalogueIndex(_products())
        cls.index.prepare()

    def random_spec(self, rng: random.Random) -> Spec:
        ix = self.index
        types = sorted(ix.type_vocab)
        spec = Spec(type_tokens=rng.sample(types, rng.choice([0, 1, 1, 2])))
        if rng.random() < 0.5:
            key = rng.choice(sorted(k for k in ix.attr_values if k not in ("size",)))
            spec.filters[key] = rng.choice(sorted(ix.attr_values[key].values()))
        prices = sorted(p.price for p in ix.products.values())
        if rng.random() < 0.5:
            spec.price_max = rng.choice(prices)
        if rng.random() < 0.3:
            spec.price_min = rng.choice(prices)
        if rng.random() < 0.2 and ix.size_values:
            spec.size = rng.choice(sorted(ix.size_values))
        if rng.random() < 0.2:
            key = rng.choice(sorted(ix.attr_values))
            spec.exclude[key] = [rng.choice(sorted(ix.attr_values[key].values()))]
        spec.in_stock_only = rng.random() < 0.2
        units = sorted({k.rsplit("_", 1)[-1] for k in ix._named_keys} & UNIT_WORDS)
        if units and rng.random() < 0.25:
            unit = rng.choice(units)
            numbers = sorted(n for n in ix._unit_table(unit)[1] if n is not None)
            if numbers:
                spec.measures = [(unit, rng.choice(numbers))]
        if rng.random() < 0.15:
            spec.desc_tokens = rng.sample(sorted(ix.desc_vocab or ix.vocab), 1)
        spec.sort = rng.choice([None, None, "price_asc", "price_desc", "rating_desc"])
        return spec

    def test_random_specs_match_the_reference(self) -> None:
        rng = random.Random(17)
        checked = 0
        for _ in range(400):
            spec = self.random_spec(rng)
            qt = set(spec.type_tokens) | set(rng.sample(sorted(self.index.vocab), 2))
            hits, _ = self.index.search(spec, qt)
            expected = reference(self.index, spec, qt)
            self.assertEqual(len(hits), len(expected), spec)
            self.assertEqual([p.id for p in hits[:3]], expected[:3], spec)
            if expected:
                self.assertEqual(hits[0].id, expected[0], spec)
            self.assertEqual([p.id for p in hits], expected, spec)
            checked += bool(expected)
        self.assertGreater(checked, 100)  # enough of the random specs found something to be a real test

    def test_budget_strategies_agree(self) -> None:
        ix = self.index
        ids = ix._posting_ids(Spec(type_tokens=[sorted(ix.type_vocab)[0]])) or set(ix.products)
        for low, high in ((None, 900), (300, None), (500, 2500), (None, None)):
            truth = {
                i
                for i in ids
                if (low is None or ix.products[i].price >= low) and (high is None or ix.products[i].price <= high)
            }
            ix._budget_sets.clear()
            ix._budgets_seen.clear()
            self.assertEqual(ix._within_budget(set(ids), low, high), truth)  # first sight: the C-level pass
            self.assertEqual(ix._within_budget(set(ids), low, high), truth)  # second: the stored band
            self.assertEqual(ix._within_budget(None, low, high) & set(ids), truth)

    def test_gender_alternatives_still_include_unisex(self) -> None:
        ix = self.index
        if GENDER_KEY in ix.attr_values:
            men = ix._filter_ids(GENDER_KEY, "men", ())
            self.assertTrue(men >= ix._attr_postings.get((GENDER_KEY, "unisex"), set()))


def _products():
    import json
    import tempfile

    from groundtruth.catalogue import load_catalogue

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(p) for p in generate_scale.make_scale_catalogue()))
    return load_catalogue(fh.name)


if __name__ == "__main__":
    unittest.main()

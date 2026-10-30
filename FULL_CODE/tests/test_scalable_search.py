"""The fast search paths (price index, lazy ranking, title anchors) must give exactly what a plain scan gives."""

from __future__ import annotations

import random
import unittest
from pathlib import Path

from groundtruth.catalogue import CatalogueIndex, Spec, load_catalogue
from groundtruth.catalogue.index import RankedHits
from groundtruth.text import tokenize

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "catalogue.jsonl"


class ScalableSearchTest(unittest.TestCase):
    """Compare the index against brute force on the bundled catalogue."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.products = load_catalogue(CATALOGUE)
        cls.index = CatalogueIndex(cls.products)

    def test_budget_index_matches_a_scan(self) -> None:
        rng = random.Random(3)
        prices = [p.price for p in self.products]
        for _ in range(200):
            low, high = rng.choice([None, rng.choice(prices)]), rng.choice([None, rng.choice(prices)])
            expected = {
                p.id for p in self.products if (low is None or p.price >= low) and (high is None or p.price <= high)
            }
            self.assertEqual(self.index._ids_in_budget(low, high), expected)

    def test_lazy_ranking_equals_full_sort(self) -> None:
        for sort in (None, "price_asc", "price_desc", "rating_desc"):
            for kind in ("shoes", "kurta", "earbuds", "backpack"):
                spec = Spec(type_tokens=[kind], sort=sort, price_max=4000)
                hits, _ = self.index.search(spec, {kind})
                full = list(hits)  # walks everything: the plain sorted order
                fresh, _ = self.index.search(spec, {kind})
                self.assertIsInstance(fresh, RankedHits)
                self.assertEqual(len(fresh), len(full))
                self.assertEqual(fresh[:3], full[:3])
                if full:
                    self.assertEqual(fresh[0], full[0])

    def test_named_products_match_the_old_full_scan(self) -> None:
        rng = random.Random(5)
        titles = [tokenize(p.title) for p in self.products]
        for _ in range(150):
            query = [w for _ in range(rng.randint(1, 3)) for w in rng.choice(titles)] + rng.sample(
                sorted(self.index.vocab), 2
            )
            self.assertEqual(self.index.mentioned(query), _mentioned_by_scan(self.index, query))


def _mentioned_by_scan(index: CatalogueIndex, query: list[str]) -> list[str]:
    """The original definition: titles of two or more words whose words all appear in the query."""
    first_at: dict[str, int] = {}
    for i, t in enumerate(query):
        first_at.setdefault(t, i)
    found = [pid for pid, title in index.title_tokens.items() if len(title) >= 2 and title <= first_at.keys()]
    found = [pid for pid in found if not any(index.title_tokens[pid] < index.title_tokens[o] for o in found)]
    return sorted(found, key=lambda pid: (min(first_at[t] for t in index.title_tokens[pid]), pid))


if __name__ == "__main__":
    unittest.main()

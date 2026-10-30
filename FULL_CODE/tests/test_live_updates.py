"""A live-updated index must answer exactly like one built from scratch on the final catalogue."""

from __future__ import annotations

import copy
import random
import threading
import unittest
from pathlib import Path

from groundtruth.answering import ShoppingAssistant
from groundtruth.catalogue import CatalogueIndex, Spec, load_catalogue
from groundtruth.concurrency import ReadWriteLock
from groundtruth.config import get_settings
from groundtruth.policy import load_policy

DATA = Path(__file__).resolve().parent.parent / "data"
SPECS = [
    Spec(type_tokens=["shoe"]),
    Spec(type_tokens=["shoe"], price_max=2500),
    Spec(type_tokens=["kurta"], price_min=500, price_max=3000),
    Spec(type_tokens=["earbud"], sort="price_asc"),
    Spec(type_tokens=["backpack"], sort="rating_desc"),
    Spec(filters={"color": "black"}),
    Spec(type_tokens=["shirt"], in_stock_only=True),
]


def ids(index: CatalogueIndex, spec: Spec) -> list[str]:
    hits, _ = index.search(spec, set(spec.type_tokens))
    return [p.id for p in hits]


class LiveUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.all = load_catalogue(DATA / "catalogue.jsonl")
        self.policy = load_policy(get_settings().paths.data_dir / "policy.txt")

    def test_adds_removes_and_price_changes_match_a_fresh_build(self) -> None:
        rng = random.Random(9)
        products = copy.deepcopy(self.all)
        rng.shuffle(products)
        start, added = products[:70], products[70:]
        removed = [p.id for p in start[:15]]
        priced = [{"id": p.id, "price": p.price + 111, "in_stock": not p.in_stock} for p in start[15:30]]

        assistant = ShoppingAssistant(CatalogueIndex(copy.deepcopy(start)), self.policy)
        done = assistant.apply_updates(add=copy.deepcopy(added), remove=removed, price_stock=copy.deepcopy(priced))
        self.assertEqual(done, {"added": len(added), "removed": 15, "price_stock": 15})

        final = {p.id: copy.deepcopy(p) for p in start if p.id not in removed}
        for change in priced:
            final[change["id"]].price, final[change["id"]].in_stock = change["price"], change["in_stock"]
        final.update({p.id: copy.deepcopy(p) for p in added})
        fresh = CatalogueIndex(list(final.values()))

        live = assistant.index
        for spec in SPECS:
            self.assertEqual(ids(live, spec), ids(fresh, spec), spec)
        self.assertEqual(live.type_vocab, fresh.type_vocab)
        self.assertEqual(live.idf, fresh.idf)
        self.assertEqual(set(live.products), set(fresh.products))
        for pid in removed:
            self.assertNotIn(pid, live.products)
            self.assertFalse(any(pid in s for s in live._text_postings.values()))

    def test_unknown_ids_are_reported_not_raised(self) -> None:
        assistant = ShoppingAssistant(CatalogueIndex(self.all), self.policy)
        done = assistant.apply_updates(remove=["NOPE"], price_stock=[{"id": "NOPE", "price": 5}])
        self.assertEqual(done, {"added": 0, "removed": 0, "price_stock": 0})

    def test_a_removed_product_is_no_longer_answered(self) -> None:
        assistant = ShoppingAssistant(CatalogueIndex(copy.deepcopy(self.all)), self.policy)
        title = assistant.index.products["P0005"].title
        self.assertIn("P0005", assistant.ask(f"price of {title}").citations)
        assistant.apply_updates(remove=["P0005"])
        self.assertNotIn("P0005", assistant.ask(f"price of {title}").citations)

    def test_questions_and_updates_can_overlap(self) -> None:
        assistant = ShoppingAssistant(CatalogueIndex(copy.deepcopy(self.all)), self.policy)
        errors: list[BaseException] = []

        def ask() -> None:
            try:
                for _ in range(60):
                    assistant.ask("black running shoes under 3000")
            except BaseException as exc:  # noqa: BLE001 - the test reports any failure from the thread
                errors.append(exc)

        threads = [threading.Thread(target=ask) for _ in range(4)]
        for t in threads:
            t.start()
        for n in range(20):
            assistant.apply_updates(price_stock=[{"id": "P0001", "price": 1000 + n}])
            assistant.apply_updates(remove=["P0002"])
            assistant.apply_updates(add=[copy.deepcopy(self.all[1])])
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class ReadWriteLockTest(unittest.TestCase):
    def test_a_writer_waits_for_readers(self) -> None:
        lock, order = ReadWriteLock(), []
        with lock.reading():
            t = threading.Thread(target=lambda: (lock.writing().__enter__(), order.append("w")))
            t.start()
            t.join(0.05)
            self.assertEqual(order, [])  # still waiting
        t.join(1)
        self.assertEqual(order, ["w"])


if __name__ == "__main__":
    unittest.main()

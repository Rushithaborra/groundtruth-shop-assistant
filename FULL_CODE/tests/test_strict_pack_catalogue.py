"""The organisers' catalogue is the single source of truth, so loading it is strict: a malformed row, a duplicate id or
a wrong product count stops the run instead of silently dropping products. Other catalogues stay tolerant."""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.errors import DataFileError

POLICY = "# Policy\n\n## 1. Returns\n\n### 1.1 Return window\nReturns within 30 days.\n"


def product(pid, price=499):
    return {
        "product_id": pid, "title": f"Voltix Spark {pid} Power Bank", "brand": "Voltix", "category": "Test",
        "sub_category": "Power Banks", "description": "A power bank.", "attributes": {}, "price_inr": price,
        "mrp_inr": price, "stock_status": "in_stock", "rating": 4.0, "review_count": 10,
    }  # fmt: skip


class StrictPackCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "policy.md").write_text(POLICY, encoding="utf-8")

    def catalogue(self, rows):
        path = self.dir / "catalogue.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def build(self, path, **kwargs):
        return build_assistant(path, self.dir / "policy.md", **kwargs)

    def as_the_pack(self, path, expected=None):
        """Settings in which `path` is the organisers' catalogue, with the given expected product count."""
        real = get_settings()
        fake = dataclasses.replace(
            real,
            paths=dataclasses.replace(real.paths, pack_catalogue=path),
            catalogue=dataclasses.replace(real.catalogue, pack_strict=True, pack_expected_products=expected),
        )
        return mock.patch("groundtruth.builder.get_settings", return_value=fake)

    def test_shipped_config_is_strict_about_the_pack(self):
        self.assertTrue(get_settings().catalogue.pack_strict)
        self.assertEqual(get_settings().catalogue.pack_expected_products, 5000)

    def test_a_duplicate_id_is_rejected_when_strict(self):
        path = self.catalogue([product("A"), product("A"), product("B")])
        with self.assertRaisesRegex(DataFileError, "duplicate id A"):
            self.build(path, strict=True)

    def test_a_malformed_row_is_rejected_when_strict(self):
        broken = product("B")
        del broken["price_inr"]
        path = self.catalogue([product("A"), broken])
        with self.assertRaises(DataFileError):
            self.build(path, strict=True)

    def test_other_catalogues_stay_tolerant_and_skip_bad_rows(self):
        path = self.catalogue([product("A"), product("A"), product("B")])
        with self.assertLogs("groundtruth.catalogue.loader", "WARNING"):
            assistant = self.build(path)
        self.assertEqual(sorted(assistant.index.products), ["A", "B"])

    def test_the_pack_catalogue_is_strict_without_being_asked(self):
        path = self.catalogue([product("A"), product("A")])
        with self.as_the_pack(path), self.assertRaisesRegex(DataFileError, "duplicate id A"):
            self.build(path)

    def test_the_pack_catalogue_must_hold_the_expected_number_of_products(self):
        path = self.catalogue([product("A"), product("B"), product("C")])
        with self.as_the_pack(path, expected=4), self.assertRaisesRegex(DataFileError, "holds 3 products, expected 4"):
            self.build(path)
        with self.as_the_pack(path, expected=3):
            self.assertEqual(len(self.build(path).index.products), 3)
        with self.as_the_pack(path, expected=None):  # no count configured: not checked
            self.assertEqual(len(self.build(path).index.products), 3)


if __name__ == "__main__":
    unittest.main()

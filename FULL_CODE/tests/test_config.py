"""Settings: the shipped config is valid, and a bad value stops start-up with a clear message."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from groundtruth.config import ConfigError, load_settings, parse_settings
from groundtruth.paths import ROOT

SHIPPED = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def with_change(section: str, key: str, value) -> dict:
    raw = copy.deepcopy(SHIPPED)
    raw[section][key] = value
    return raw


class ConfigTests(unittest.TestCase):
    def test_shipped_config_loads_with_paths_resolved_under_full_code(self):
        settings = parse_settings(copy.deepcopy(SHIPPED))
        self.assertEqual(settings.assistant.list_limit, 3)
        self.assertEqual(settings.server.port, 8000)
        self.assertEqual(settings.paths.catalogue, ROOT / "data" / "catalogue.jsonl")
        self.assertTrue(settings.paths.vocabulary.exists())

    def test_out_of_range_and_wrong_type_values_are_rejected(self):
        for section, key, value in [
            ("server", "port", 0),
            ("server", "port", 70000),
            ("server", "port", "8000"),
            ("assistant", "list_limit", True),
            ("search", "typo_cutoff", 1.5),
            ("logging", "level", "LOUD"),
            ("submission", "team_id", "TEAM 802/x"),
            ("server", "host", ""),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError) as ctx:
                parse_settings(with_change(section, key, value))
            self.assertIn(f"{section}.{key}", str(ctx.exception))

    def test_missing_and_unknown_keys_are_named(self):
        raw = copy.deepcopy(SHIPPED)
        del raw["assistant"]["clarify_limit"]
        raw["assistant"]["clarify_limt"] = 5
        with self.assertRaises(ConfigError) as ctx:
            parse_settings(raw)
        self.assertIn("clarify_limit", str(ctx.exception))
        self.assertIn("clarify_limt", str(ctx.exception))

    def test_missing_section_is_rejected(self):
        raw = copy.deepcopy(SHIPPED)
        del raw["server"]
        with self.assertRaises(ConfigError):
            parse_settings(raw)

    def test_evaluation_weights_must_add_up_to_one(self):
        with self.assertRaises(ConfigError):
            parse_settings(with_change("evaluation", "weight_refusal", 0.5))

    def test_environment_overrides_the_file_and_is_validated(self):
        with mock.patch.dict(os.environ, {"GROUNDTRUTH_PORT": "9001", "GROUNDTRUTH_EMBED_MODEL": "models/e"}):
            settings = load_settings(ROOT / "config.json")
        self.assertEqual(settings.server.port, 9001)
        self.assertEqual(settings.models.embed_model, "models/e")
        with mock.patch.dict(os.environ, {"GROUNDTRUTH_PORT": "eighty"}), self.assertRaises(ConfigError):
            load_settings(ROOT / "config.json")

    def test_unreadable_files_raise_config_error(self):
        with self.assertRaises(ConfigError):
            load_settings(ROOT / "no-such-config.json")
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "config.json"
            broken.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(broken)


if __name__ == "__main__":
    unittest.main()

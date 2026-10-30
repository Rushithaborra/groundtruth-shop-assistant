"""The submission zip: the guidelines' checklist catches each problem, and the zip holds one teamID_psID folder."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.make_submission_zip import README_SECTIONS, check_project, zip_folder

PREDICTION = {
    "question_id": "Q-1",
    "action": "refuse",
    "answer_text": "I don't have that.",
    "cited_product_ids": [],
    "cited_policy_sections": [],
    "stated_prices": [],
    "stated_facts": [],
    "latency_ms": 1.0,
    "tokens_in": 0,
    "tokens_out": 0,
}


def make_project(root: Path) -> Path:
    """A minimal project that passes every check."""
    readme = "# Team\n\n" + "".join(f"## {s}\n\ntext\n\n" for s in README_SECTIONS)
    files = {
        "README.md": readme,
        ".gitignore": ".env\n",
        "LICENSE": "MIT\n",
        "submission/R3_predictions.jsonl": json.dumps(PREDICTION) + "\n",
        "FULL_CODE/main.py": "",
        "FULL_CODE/uv.lock": "",
        "FULL_CODE/.env.example": "",
        "FULL_CODE/pyproject.toml": "",
        "questions_test.csv": "question_id,conversation_id,turn,question_text\nQ-1,C-1,1,iphone price\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self._tmp.name) / "TEAM-1_R3")
        self.questions = (self.root / "questions_test.csv").rename(Path(self._tmp.name) / "questions_test.csv")

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, rel: str, text: str = "x") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_clean_project_passes(self):
        self.assertEqual(check_project(self.root, self.questions), [])

    def test_secrets_data_models_and_junk_are_caught(self):
        for rel in (
            ".env",
            "FULL_CODE/.venv/bin/python",
            "FULL_CODE/data/pack/catalogue.csv",
            "FULL_CODE/models/qwen.gguf",
            "FULL_CODE/output/old.zip",
            ".DS_Store",
        ):
            self.add(rel)
        problems = "\n".join(check_project(self.root, self.questions))
        for rel in (".env", ".venv", "data/pack", "FULL_CODE/models", "old.zip", ".DS_Store"):
            self.assertIn(rel, problems)

    def test_extra_top_level_items_and_missing_files_are_caught(self):
        self.add("notes.txt")
        (self.root / "FULL_CODE" / "uv.lock").unlink()
        problems = check_project(self.root, self.questions)
        self.assertIn("unexpected top-level item: notes.txt", problems)
        self.assertIn("missing FULL_CODE/uv.lock", problems)

    def test_only_the_guidelines_items_may_sit_at_the_top(self):
        # page 2 of the guidelines: README.md, .gitignore, submission/, FULL_CODE/ and an optional LICENSE
        self.add(".gitattributes")
        self.add("FULL_CODE/.gitattributes")  # inside FULL_CODE it is fine
        problems = check_project(self.root, self.questions)
        self.assertEqual([p for p in problems if "gitattributes" in p], ["unexpected top-level item: .gitattributes"])

    def test_a_readme_without_a_required_section_is_caught(self):
        self.add("README.md", "# Team\n\n## What it does\n")
        problems = check_project(self.root, self.questions)
        self.assertIn("README is missing the section 'Limitations'", problems)

    def test_predictions_must_cover_every_test_question(self):
        self.questions.write_text(self.questions.read_text() + "Q-2,C-2,1,hi\n", encoding="utf-8")
        self.assertTrue(any("not valid" in p for p in check_project(self.root, self.questions)))

    def test_the_zip_holds_one_team_folder(self):
        zip_path = zip_folder(self.root, Path(self._tmp.name) / "out" / "TEAM-1_R3.zip")
        names = zipfile.ZipFile(zip_path).namelist()
        self.assertEqual({n.split("/")[0] for n in names}, {"TEAM-1_R3"})
        self.assertIn("TEAM-1_R3/submission/R3_predictions.jsonl", names)


if __name__ == "__main__":
    unittest.main()

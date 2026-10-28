"""The problem pack's question files: questions_dev.csv / questions_test.csv and the dev truth file.

Each CSV row is one shopper turn. Rows that share a conversation_id are turns of one conversation, and later turns
depend on earlier ones, so they are answered in turn order within one session.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

QUESTION_COLUMNS = ("question_id", "conversation_id", "turn", "question_text")


@dataclass(frozen=True)
class Question:
    question_id: str
    conversation_id: str
    turn: int
    text: str


def load_questions(path: str | Path) -> list[Question]:
    """Read a question CSV; raises ValueError naming the problem if a column or value is missing or invalid."""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in QUESTION_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(missing)}")
        questions, seen = [], set()
        for n, row in enumerate(reader, 2):  # line 1 is the header
            qid = (row["question_id"] or "").strip()
            if not qid or qid in seen:
                raise ValueError(f"{path} line {n}: missing or duplicate question_id {qid!r}")
            try:
                turn = int(row["turn"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path} line {n}: turn must be a whole number, got {row['turn']!r}") from exc
            seen.add(qid)
            questions.append(Question(qid, (row["conversation_id"] or qid).strip(), turn, row["question_text"] or ""))
    return questions


def conversations(questions: list[Question]) -> list[list[Question]]:
    """Group turns by conversation, in the order conversations first appear, each sorted by turn."""
    grouped: dict[str, list[Question]] = {}
    for q in questions:
        grouped.setdefault(q.conversation_id, []).append(q)
    return [sorted(turns, key=lambda q: q.turn) for turns in grouped.values()]


def load_truth(path: str | Path) -> dict[str, dict]:
    """The dev truth file (JSONL), keyed by question_id."""
    truth = {}
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if "question_id" not in row or "expected_action" not in row:
                raise ValueError(f"{path} line {n}: needs question_id and expected_action")
            truth[row["question_id"]] = row
    return truth

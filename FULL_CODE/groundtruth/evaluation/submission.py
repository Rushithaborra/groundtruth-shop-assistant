"""Write predictions in the problem pack's submission format (pack README, section 7.2).

One JSON line per question. Every citation, price and fact comes from the reply's structured claims, which the
reply builders copy from the catalogue record; nothing is parsed back out of the reply text. Lines are validated
before anything is written, so a malformed file is never produced.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from ..answering import ANSWER, CLARIFY, REFUSE, Response, Session, ShoppingAssistant
from ..arguments import add_model_arguments
from ..builder import build_assistant
from ..config import get_settings
from ..console import write_out
from .questions import Question, conversations, load_questions

ACTIONS = (ANSWER, CLARIFY, REFUSE)
REQUIRED_KEYS = {
    "question_id": str,
    "action": str,
    "answer_text": str,
    "cited_product_ids": list,
    "cited_policy_sections": list,
    "stated_prices": list,
    "stated_facts": list,
    "latency_ms": (int, float),
    "tokens_in": int,
    "tokens_out": int,
}


def _number(value: float) -> int | float:
    """Whole rupee amounts as integers, as the catalogue writes them."""
    return int(value) if float(value).is_integer() else value


def prediction(question: Question, response: Response) -> dict:
    """One submission line for one answered question."""
    claims = response.claims
    return {
        "question_id": question.question_id,
        "action": response.action,
        "answer_text": response.text,
        "cited_product_ids": list(claims.products),
        "cited_policy_sections": list(claims.sections),
        "stated_prices": [{"product_id": pid, "price_inr": _number(price)} for pid, price in claims.prices],
        "stated_facts": [{"product_id": pid, "field": field, "value": value} for pid, field, value in claims.facts],
        "latency_ms": round(response.latency_ms, 3),
        "tokens_in": response.llm_input_tokens,
        "tokens_out": response.llm_output_tokens,
    }


def predict(assistant: ShoppingAssistant, questions: list[Question]) -> list[dict]:
    """Answer every question; turns of one conversation share a session, so earlier constraints carry over."""
    by_id = {}
    for turns in conversations(questions):
        session = Session()
        for q in turns:
            by_id[q.question_id] = prediction(q, assistant.ask(q.text, session))
    return [by_id[q.question_id] for q in questions]  # same order as the question file


def validate_predictions(rows: list[dict], questions: list[Question]) -> None:
    """Raise ValueError naming the first problem: a missing, duplicate or unknown question, or a bad key or value."""
    expected = [q.question_id for q in questions]
    seen = [r.get("question_id") for r in rows]
    if sorted(seen) != sorted(expected) or len(set(seen)) != len(seen):
        missing, extra = set(expected) - set(seen), set(seen) - set(expected)
        raise ValueError(f"predictions must cover each question once: missing {sorted(missing)}, extra {sorted(extra)}")
    for row in rows:
        qid = row["question_id"]
        for key, kind in REQUIRED_KEYS.items():
            if key not in row or isinstance(row[key], bool) or not isinstance(row[key], kind):
                raise ValueError(f"{qid}: '{key}' is missing or has the wrong type")
        if row["action"] not in ACTIONS:
            raise ValueError(f"{qid}: action must be one of {', '.join(ACTIONS)}")
        if (
            row["latency_ms"] < 0
            or not math.isfinite(row["latency_ms"])
            or min(row["tokens_in"], row["tokens_out"]) < 0
        ):
            raise ValueError(f"{qid}: latency and token counts must be non-negative numbers")
        cited = set(row["cited_product_ids"])
        for price in row["stated_prices"]:
            if price.get("product_id") not in cited or not isinstance(price.get("price_inr"), (int, float)):
                raise ValueError(f"{qid}: every stated price needs a cited product_id and a numeric price_inr")
        for fact in row["stated_facts"]:
            if fact.get("product_id") not in cited or not isinstance(fact.get("field"), str) or "value" not in fact:
                raise ValueError(f"{qid}: every stated fact needs a cited product_id, a field and a value")


def write_predictions(rows: list[dict], path: str | Path) -> Path:
    """Write the predictions as UTF-8 JSON lines, creating the folder if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The submit command's options: which split, data files, output file and models."""
    paths = get_settings().paths
    ap.add_argument(
        "--split",
        choices=("test", "dev"),
        default="test",
        help="test: the 150 scored questions -> submission/R3_predictions.jsonl; dev: the 40 practice ones",
    )
    ap.add_argument("--catalogue", default=str(paths.pack_catalogue), help="the pack's catalogue")
    ap.add_argument("--policy", default=str(paths.pack_policy), help="the pack's policy")
    ap.add_argument("--out", help="output file (default depends on --split; see config.json)")
    add_model_arguments(ap)


def run(args: argparse.Namespace) -> None:
    """Answer every question in turn order, validate the lines against the pack's format, and write them."""
    paths = get_settings().paths
    questions_path = paths.test_questions if args.split == "test" else paths.dev_questions
    out = Path(args.out) if args.out else (paths.submission if args.split == "test" else paths.dev_predictions)
    questions = load_questions(questions_path)
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model)
    rows = predict(assistant, questions)
    validate_predictions(rows, questions)
    write_predictions(rows, out)
    actions = ", ".join(f"{a} {n}" for a, n in sorted(Counter(r["action"] for r in rows).items()))
    write_out(f"wrote {len(rows)} predictions to {out.resolve()} ({actions})")

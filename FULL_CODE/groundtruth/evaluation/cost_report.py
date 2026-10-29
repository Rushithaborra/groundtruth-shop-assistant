"""Per-query latency and token-cost report (required output 5), over the pack's dev and test questions.

For every question: the path that answered it (rules / embedding / LLM), wall-clock latency, the model tokens it
really used, and an estimate of the tokens a reply involves (the query plus the catalogue records and policy text it
cites, and the reply itself; about four characters per token). Test questions are only answered and timed; nothing
is read from their expected behaviour.

Beside it, a labelled ESTIMATE of the cost if an LLM wrote every reply instead, anchored to one measured call of the
provided 0.5B instruct model on a laptop CPU (config.json, cost_report section).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from ..answering import Session, ShoppingAssistant
from ..arguments import add_model_arguments
from ..builder import build_assistant
from ..config import get_settings
from ..console import write_out
from ..metrics import percentile
from ..text import approx_tokens
from .questions import Question, conversations, load_questions

FIELDS = (
    "question_id",
    "split",
    "conversation_id",
    "turn",
    "action",
    "path",
    "latency_ms",
    "model_tokens_in",
    "model_tokens_out",
    "est_context_tokens",
    "est_reply_tokens",
    "llm_baseline_tokens_in",
    "llm_baseline_seconds",
)


def measure(assistant: ShoppingAssistant, questions_by_split: dict[str, list[Question]]) -> list[dict]:
    """Answer every question (conversation turns in order) and record what each reply cost."""
    baseline = get_settings().cost_report
    rows = []
    for split, questions in questions_by_split.items():
        for turns in conversations(questions):
            session = Session()
            for q in turns:
                r = assistant.ask(q.text, session)
                rows.append(
                    {
                        "question_id": q.question_id,
                        "split": split,
                        "conversation_id": q.conversation_id,
                        "turn": q.turn,
                        "action": r.action,
                        "path": r.path,
                        "latency_ms": round(r.latency_ms, 3),
                        "model_tokens_in": r.llm_input_tokens,
                        "model_tokens_out": r.llm_output_tokens,
                        "est_context_tokens": r.context_tokens,
                        "est_reply_tokens": r.output_tokens,
                        # an LLM writing this reply: the measured grounded prompt plus this query, one call
                        "llm_baseline_tokens_in": baseline.llm_prompt_tokens + approx_tokens(q.text),
                        "llm_baseline_seconds": baseline.llm_seconds_per_call,
                    }
                )
    return rows


def summarise(rows: list[dict]) -> dict:
    """Totals, averages and latency percentiles for the measured assistant and the LLM-every-query estimate."""
    n = len(rows)
    lat = [r["latency_ms"] for r in rows]
    model_in, model_out = sum(r["model_tokens_in"] for r in rows), sum(r["model_tokens_out"] for r in rows)
    base_in = sum(r["llm_baseline_tokens_in"] for r in rows)
    base_out = get_settings().cost_report.llm_output_tokens * n
    base_seconds = sum(r["llm_baseline_seconds"] for r in rows)
    return {
        "questions": n,
        "by_split": {s: sum(r["split"] == s for r in rows) for s in sorted({r["split"] for r in rows})},
        "path_share": {p: round(sum(r["path"] == p for r in rows) / n, 4) for p in ("rules", "semantic", "llm")},
        "latency_ms": {
            "mean": round(statistics.mean(lat), 3),
            "p50": round(percentile(lat, 0.5), 3),
            "p95": round(percentile(lat, 0.95), 3),
            "max": round(max(lat), 3),
            "total": round(sum(lat), 1),
        },
        "model_tokens_measured": {"in": model_in, "out": model_out, "per_query": round((model_in + model_out) / n, 1)},
        "context_tokens_estimated": {
            "mean": round(statistics.mean(r["est_context_tokens"] for r in rows), 1),
            "mean_reply": round(statistics.mean(r["est_reply_tokens"] for r in rows), 1),
            "note": "about 4 characters per token; what a reply cites and says, not tokens sent to any model",
        },
        "llm_every_query_ESTIMATE": {
            "tokens_in": base_in,
            "tokens_out": base_out,
            "seconds": round(base_seconds, 1),
            "note": "estimate: one measured call of the provided 0.5B model (prompt holding the catalogue's "
            "allowed values) times the number of queries, on a laptop CPU; not run for every query",
        },
        "budget": "the pack does not state a token or cost budget; GroundTruth uses 0 model tokens per query",
    }


def write_report(rows: list[dict], summary: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write the per-query CSV and the summary JSON; returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    per_query, totals = out_dir / "cost_report.csv", out_dir / "cost_report.json"
    with per_query.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    totals.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return per_query, totals


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The report command's options: data files, which question split, and models."""
    paths = get_settings().paths
    ap.add_argument("--catalogue", default=str(paths.pack_catalogue), help="the pack's catalogue")
    ap.add_argument("--policy", default=str(paths.pack_policy), help="the pack's policy")
    ap.add_argument("--split", choices=("all", "dev", "test"), default="all", help="which question files to run")
    add_model_arguments(ap)


def run(args: argparse.Namespace) -> None:
    """Answer the pack's questions, then write the per-query latency and token report and its summary."""
    paths = get_settings().paths
    files = {"dev": paths.dev_questions, "test": paths.test_questions}
    chosen = files if args.split == "all" else {args.split: files[args.split]}
    assistant = build_assistant(
        args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model, measure_tokens=True
    )
    rows = measure(assistant, {split: load_questions(path) for split, path in chosen.items()})
    summary = summarise(rows)
    per_query, totals = write_report(rows, summary, paths.reports_dir)
    write_out(json.dumps(summary, indent=2))
    write_out(f"\nper-query report: {per_query}\nsummary: {totals}")

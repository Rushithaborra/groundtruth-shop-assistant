"""Score the assistant on groundedness, helpfulness and refusal accuracy, plus a latency / token-cost report.

    python main.py evaluate [--questions data/test_questions.jsonl] [--out output/reports]

Question format (one JSON object per line); extra keys are ignored:
    {"id": "q1", "turns": ["...", "..."],            # or "question": "..."  (a single turn)
     "expected": {"behaviour": "answer" | "clarify" | "refuse",
                  "product_ids": [...],              # all must be cited (helpfulness)
                  "valid_ids": [...],                # every product fitting the request; cited ids must be among them
                  "policy_sections": [...],          # at least one must be cited
                  "must_include": ["₹2,499", ...]}}  # substrings (case-insensitive) the reply must contain
Adapt `load_cases` if the real test file uses other field names.

Scoring
  groundedness   share of replies with zero unsupported claims (checked from the reply text, not from the
                 assistant's own bookkeeping): every citation resolves, every ₹ amount belongs to a cited
                 product / policy section / the user's own query, every other number appears in the cited
                 sources, every product title named is cited, every factual sentence carries a citation.
  helpfulness    over questions that should be answered: cited the right product(s)/policy, satisfied the
                 filters (precision), and included the required facts.
  refusal acc.   behaviour (answer / clarify / refuse) matches expectation, over all questions.
  hallucinated prices are counted separately and subtract 0.10 from the final score each, floor 0.
  final          weighted sum minus the hallucinated-price penalty; weights and penalty are in config.json
                 (evaluation section; default 0.35 / 0.35 / 0.30 and 0.10)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from ..answering import Response, Session, ShoppingAssistant
from ..arguments import add_data_arguments, add_model_arguments
from ..builder import build_assistant
from ..config import get_settings
from ..console import write_out
from ..grounding import Grounding


def load_cases(path: Path) -> list[dict]:
    """Synthetic question cases (JSONL), accepting a few field spellings."""
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        turns = raw.get("turns") or [raw.get("question") or raw.get("query") or raw.get("q")]
        expected = raw.get("expected") or {
            k: v for k, v in raw.items() if k not in {"id", "turns", "question", "query"}
        }
        cases.append({"id": raw.get("id", f"q{len(cases) + 1}"), "turns": turns, "expected": expected})
    return cases


def helpfulness(resp: Response, exp: dict, cited: list[str]) -> float:
    """Share of the expected facts, products and policy sections the reply got right (0 to 1)."""
    if exp.get("behaviour") != "answer":
        return 1.0 if resp.action == exp.get("behaviour") else 0.0
    if resp.action != "answer":
        return 0.0
    checks = []
    ids = [c for c in cited if not c.startswith("policy:")]
    if exp.get("product_ids"):
        checks.append(all(i in ids for i in exp["product_ids"]))
    if exp.get("valid_ids") is not None:
        checks.append(bool(ids) and all(i in exp["valid_ids"] for i in ids))
    if exp.get("policy_sections"):
        checks.append(any(c == f"policy:{s}" for s in exp["policy_sections"] for c in cited))
    low = resp.text.lower()
    for needle in exp.get("must_include", []):
        checks.append(needle.lower() in low)
    return sum(checks) / len(checks) if checks else 1.0


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The evaluate command's options: data, models, question set, output folder, token prices."""
    paths = get_settings().paths
    add_data_arguments(ap)
    add_model_arguments(ap)
    ap.add_argument("--questions", default=str(paths.questions))
    ap.add_argument("--out", default=str(paths.reports_dir))
    ap.add_argument(
        "--price-in", type=float, default=0.0, help="USD per 1M input tokens (0 for the rules-based assistant)"
    )
    ap.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
    ap.add_argument("--verbose", action="store_true", help="print every failing case")


def run(args: argparse.Namespace) -> None:
    """Score a question set with the local evaluator and write its reports."""
    assistant = build_assistant(
        args.catalogue,
        args.policy,
        embed_model=args.embed_model,
        llm_model=args.llm_model,
        resolver=args.llm_resolver,
        measure_tokens=True,
    )
    grounding = Grounding(assistant.index, assistant.sections)
    rows = [score_case(assistant, grounding, case) for case in load_cases(Path(args.questions))]
    summary = summarise(rows, args.price_in, args.price_out)
    out_dir = write_reports(rows, summary, Path(args.out))
    write_out(json.dumps(summary, indent=2, ensure_ascii=False))
    report_failures(rows, out_dir, args.verbose)


def score_case(assistant: ShoppingAssistant, grounding: Grounding, case: dict) -> dict:
    """Ask one case's turns in one session and score the last reply."""
    session, resp = Session(), None
    for turn in case["turns"]:
        resp = assistant.ask(turn, session)
    exp = case["expected"]
    accept = exp["behaviour"] if isinstance(exp.get("behaviour"), list) else [exp.get("behaviour")]
    exp = {**exp, "behaviour": resp.action if resp.action in accept else accept[0]}
    g = grounding.check(resp.text, " ".join(case["turns"]))
    return {
        "id": case["id"],
        "query": " | ".join(case["turns"]),
        "expected": exp.get("behaviour"),
        "action": resp.action,
        "behaviour_ok": resp.action in accept,
        "grounded": not g["problems"],
        "hallucinated_prices": g["hallucinated_prices"],
        "helpfulness": helpfulness(resp, exp, g["cited"]),
        "problems": "; ".join(g["problems"]),
        "latency_ms": round(resp.latency_ms, 3),
        "context_tokens": resp.context_tokens,
        "output_tokens": resp.output_tokens,
        "path": resp.path,
        "llm_input_tokens": resp.llm_input_tokens,
        "llm_output_tokens": resp.llm_output_tokens,
        "reply": resp.text,
    }


def _scores(rows: list[dict]) -> dict:
    """The three scores, the final weighted score, and how often each kind of reply was right."""
    n = len(rows)
    answerable = [r for r in rows if r["expected"] == "answer"]
    groundedness = sum(r["grounded"] for r in rows) / n
    helpful = sum(r["helpfulness"] for r in answerable) / max(1, len(answerable))
    refusal_acc = sum(r["behaviour_ok"] for r in rows) / n
    hallucinated = sum(r["hallucinated_prices"] for r in rows)
    w = get_settings().evaluation
    final = max(
        0.0,
        w.weight_groundedness * groundedness
        + w.weight_helpfulness * helpful
        + w.weight_refusal * refusal_acc
        - w.hallucinated_price_penalty * hallucinated,
    )

    def rate(expected: str) -> float:
        sub = [r for r in rows if r["expected"] == expected]
        return (sum(r["behaviour_ok"] for r in sub) / len(sub)) if sub else float("nan")

    return {
        "groundedness": round(groundedness, 4),
        "helpfulness": round(helpful, 4),
        "refusal_accuracy": round(refusal_acc, 4),
        "hallucinated_prices": hallucinated,
        "final_score": round(final, 4),
        "correct_answer_rate": rate("answer"),
        "correct_clarify_rate": rate("clarify"),
        "correct_refusal_rate": rate("refuse"),
    }


def _latency_stats(latencies: list[float]) -> dict:
    """Mean, median, 95th percentile and maximum latency, in milliseconds."""
    return {
        "mean": round(statistics.mean(latencies), 3),
        "p50": round(statistics.median(latencies), 3),
        "p95": round(statistics.quantiles(latencies, n=20)[-1], 3),
        "max": round(max(latencies), 3),
    }


def _token_stats(context: list[int], output: list[int]) -> dict:
    """Estimated context and reply tokens (about four characters per token)."""
    return {
        "context_mean": round(statistics.mean(context), 1),
        "output_mean": round(statistics.mean(output), 1),
        "context_total": sum(context),
        "output_total": sum(output),
        "note": "estimates (~4 chars/token): context = query + cited sources an LLM would be given; "
        "this assistant makes zero LLM calls",
    }


def _confusion(rows: list[dict]) -> dict:
    """How many replies of each (expected, actual) behaviour pair there were."""
    counts: dict = {}
    for r in rows:
        counts[(r["expected"], r["action"])] = counts.get((r["expected"], r["action"]), 0) + 1
    return {f"{a}->{b}": c for (a, b), c in sorted(counts.items(), key=str)}


def summarise(rows: list[dict], price_in: float, price_out: float) -> dict:
    """Scores, behaviour rates, latency and token totals over all scored cases."""
    n = len(rows)
    ctx = [r["context_tokens"] for r in rows]
    out = [r["output_tokens"] for r in rows]
    cost = sum(c * price_in + o * price_out for c, o in zip(ctx, out)) / 1e6
    return {
        "questions": n,
        **_scores(rows),
        "latency_ms": _latency_stats([r["latency_ms"] for r in rows]),
        "tokens_per_query": _token_stats(ctx, out),
        "path_share": {
            p: round(sum(r["path"] == p for r in rows) / n, 4) for p in ("rules", "synonym", "semantic", "llm")
        },
        "llm_tokens_real": {
            "input_total": sum(r["llm_input_tokens"] for r in rows),
            "output_total": sum(r["llm_output_tokens"] for r in rows),
            "calls": sum(r["path"] == "llm" for r in rows),
            "note": "measured from the instruct model's tokenizer; 0 when no model is configured",
        },
        "estimated_cost_usd": round(cost, 6),
        "confusion": _confusion(rows),
    }


def write_reports(rows: list[dict], summary: dict, out_dir: Path) -> Path:
    """query_report.csv (one row per case) and summary.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "query_report.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_dir


def report_failures(rows: list[dict], out_dir: Path, verbose: bool) -> None:
    """Print how many cases failed, and each failure when verbose."""
    failing = [r for r in rows if not (r["behaviour_ok"] and r["grounded"] and r["helpfulness"] == 1.0)]
    write_out(f"\n{len(failing)} of {len(rows)} questions have a failure (see {out_dir / 'query_report.csv'})")
    if verbose:
        for r in failing:
            write_out(
                f"\n[{r['id']}] {r['query']}\n  expected={r['expected']} got={r['action']} help={r['helpfulness']:.2f} "
                f"grounded={r['grounded']} {r['problems']}\n  reply: {r['reply'][:300]}"
            )

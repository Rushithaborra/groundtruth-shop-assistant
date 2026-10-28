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

from ..answering import Session
from ..arguments import add_data_arguments, add_model_arguments
from ..builder import build_assistant
from ..config import get_settings
from ..console import write_out
from ..grounding import Grounding


def load_cases(path: Path) -> list[dict]:
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


def helpfulness(resp, exp: dict, cited: list[str]) -> float:
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
    assistant = build_assistant(
        args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model, measure_tokens=True
    )
    grounding = Grounding(assistant.index, assistant.sections)
    cases = load_cases(Path(args.questions))
    rows, confusion = [], {}
    for case in cases:
        session, resp = Session(), None
        for turn in case["turns"]:
            resp = assistant.ask(turn, session)
        exp = case["expected"]
        accept = exp["behaviour"] if isinstance(exp.get("behaviour"), list) else [exp.get("behaviour")]
        exp = {**exp, "behaviour": resp.action if resp.action in accept else accept[0]}
        g = grounding.check(resp.text, " ".join(case["turns"]))
        behaviour_ok = resp.action in accept
        confusion[(exp.get("behaviour"), resp.action)] = confusion.get((exp.get("behaviour"), resp.action), 0) + 1
        rows.append(
            {
                "id": case["id"],
                "query": " | ".join(case["turns"]),
                "expected": exp.get("behaviour"),
                "action": resp.action,
                "behaviour_ok": behaviour_ok,
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
        )

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

    def rate(expected: str):
        sub = [r for r in rows if r["expected"] == expected]
        return (sum(r["behaviour_ok"] for r in sub) / len(sub)) if sub else float("nan"), len(sub)

    lat = [r["latency_ms"] for r in rows]
    ctx = [r["context_tokens"] for r in rows]
    out = [r["output_tokens"] for r in rows]
    cost = sum(c * args.price_in + o * args.price_out for c, o in zip(ctx, out)) / 1e6
    summary = {
        "questions": n,
        "groundedness": round(groundedness, 4),
        "helpfulness": round(helpful, 4),
        "refusal_accuracy": round(refusal_acc, 4),
        "hallucinated_prices": hallucinated,
        "final_score": round(final, 4),
        "correct_answer_rate": rate("answer")[0],
        "correct_clarify_rate": rate("clarify")[0],
        "correct_refusal_rate": rate("refuse")[0],
        "latency_ms": {
            "mean": round(statistics.mean(lat), 3),
            "p50": round(statistics.median(lat), 3),
            "p95": round(statistics.quantiles(lat, n=20)[-1], 3),
            "max": round(max(lat), 3),
        },
        "tokens_per_query": {
            "context_mean": round(statistics.mean(ctx), 1),
            "output_mean": round(statistics.mean(out), 1),
            "context_total": sum(ctx),
            "output_total": sum(out),
            "note": "estimates (~4 chars/token): context = query + cited sources an LLM would be given; "
            "this assistant makes zero LLM calls",
        },
        "path_share": {p: round(sum(r["path"] == p for r in rows) / n, 4) for p in ("rules", "semantic", "llm")},
        "llm_tokens_real": {
            "input_total": sum(r["llm_input_tokens"] for r in rows),
            "output_total": sum(r["llm_output_tokens"] for r in rows),
            "calls": sum(r["path"] == "llm" for r in rows),
            "note": "measured from the instruct model's tokenizer; 0 when no model is configured",
        },
        "estimated_cost_usd": round(cost, 6),
        "confusion": {f"{a}->{b}": c for (a, b), c in sorted(confusion.items(), key=str)},
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "query_report.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    write_out(json.dumps(summary, indent=2, ensure_ascii=False))
    failing = [r for r in rows if not (r["behaviour_ok"] and r["grounded"] and r["helpfulness"] == 1.0)]
    write_out(f"\n{len(failing)} of {n} questions have a failure (see {out_dir / 'query_report.csv'})")
    if args.verbose:
        for r in failing:
            write_out(
                f"\n[{r['id']}] {r['query']}\n  expected={r['expected']} got={r['action']} help={r['helpfulness']:.2f} "
                f"grounded={r['grounded']} {r['problems']}\n  reply: {r['reply'][:300]}"
            )

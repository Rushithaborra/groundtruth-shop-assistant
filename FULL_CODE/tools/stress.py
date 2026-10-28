"""A stress set built from the pack's catalogue (never from its dev or test questions), scored by the official scorer.

    python main.py stress --part tune       # questions on half the sampled products: fix failures found here
    python main.py stress --part holdout    # different products, same kinds of question: measure only

Shoppers type product names badly (the pack README: "ironnest ripple flex" for Ironnest RippleFlex, "krida" for
Kridaa), mix Hinglish in, and ask for things the store doesn't sell or facts the catalogue doesn't list. Each
question here applies one of those to a sampled product, and its expected behaviour is worked out by plain filtering
of the catalogue, never by asking the assistant: one match answers, several matches at different prices clarify,
a missing attribute or an unsold item refuses.

Written by the same people as the code, so this measures robustness to these phrasings, not general accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re

from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.console import write_out
from groundtruth.evaluation.official import _require_scorer_dependencies, score_file
from groundtruth.evaluation.questions import Question
from groundtruth.evaluation.submission import predict, write_predictions

SEED = 2026
PRICE_ASKS = ("{name} kitne ka hai", "{name} price", "{name} ka rate kya hai", "price of {name}", "{name} kitne ki")
STOCK_ASKS = ("{name} stock me hai?", "is {name} in stock", "{name} available hai kya")
MATERIAL_ASKS = ("{name} kis material ka hai?", "what is the material of {name}?")
WATER_ASKS = ("{name} paani me chalega?", "is {name} waterproof?")
VAGUE_ASKS = ("{colour} {kind} kitne ka", "price of the {colour} {kind}")
NOT_SOLD = ("iphone 15", "redmi note 13 pro", "samsung tv 43 inch", "dell laptop", "aashirvaad atta 5kg",
            "dolo 650 strip", "gold chain", "silver payal", "honda activa", "maggi noodles", "hp printer ink",
            "nike air max", "boat airdopes", "playstation 5")  # fmt: skip
NOT_SOLD_ASKS = ("{item} hai kya", "{item} price", "do you sell {item}", "{item} milega?")


def _split_line(word: str) -> str:
    """ "RippleFlex" -> "ripple flex", as shoppers type it."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word).lower()


def _drop_letter(word: str, rng: random.Random) -> str:
    """A one-letter typo inside a longer word ("Kridaa" -> "Krida"); short words stay as they are."""
    if len(word) < 6:
        return word
    i = rng.randrange(1, len(word) - 1)
    return word[:i] + word[i + 1 :]


def _identity(p: dict) -> tuple:
    """What a shopper names: brand, product line, colour, sub-category."""
    return (p["brand"], p["title"].split()[1], p["attributes"].get("colour"), p["sub_category"])


def _name(p: dict, style: int, rng: random.Random) -> str:
    """The product as a shopper might type it, in one of four styles."""
    brand, line, colour, sub = _identity(p)
    kind = sub.lower()
    colour = f" {colour}" if colour else ""
    if style == 0:
        return f"{brand} {line}{colour} {kind}"  # exact
    if style == 1:
        return f"{brand.lower()} {_split_line(line)}{colour} {kind}"  # line name split
    if style == 2:
        return f"{_drop_letter(brand, rng).lower()} {line.lower()}{colour} {kind}"  # brand typo
    return f"{brand.lower()} {_split_line(line)}{colour}"  # no product word


def _part_of(key: str) -> str:
    """Assign a question to the tune or holdout part by hashing its key, so the split is stable."""
    return "tune" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "holdout"


def build(catalogue: list[dict], n_products: int = 120, seed: int = SEED) -> list[dict]:
    """Truth rows (the official scorer's format) for questions about sampled products and unsold items."""
    rng = random.Random(seed)
    by_identity: dict[tuple, list[dict]] = {}
    for p in catalogue:
        by_identity.setdefault(_identity(p), []).append(p)
    rows: list[dict] = []

    def add(text: str, action: str, qtype: str, part: str, products=(), facts=(), candidates=()) -> None:
        """Add one stress question with its expected action, type, part and the products and facts it needs."""
        rows.append({
            "question_id": f"S-{len(rows):04d}", "conversation_id": f"SC-{len(rows):04d}", "turn": 1,
            "question_text": text, "expected_action": action, "question_type": qtype, "part": part,
            "acceptable_product_ids": list(products), "required_facts": list(facts),
            "clarify_candidate_ids": list(candidates), "policy_sections": [],
        })  # fmt: skip

    for p in rng.sample(catalogue, n_products):
        part, matches = _part_of(p["product_id"]), by_identity[_identity(p)]
        name = _name(p, rng.randrange(4), rng)
        if len(matches) > 1:
            if len({m["price_inr"] for m in matches}) > 1:  # same name, different prices: ask which one
                ids = [m["product_id"] for m in matches]
                add(rng.choice(PRICE_ASKS).format(name=name), "clarify", "clarify_ambiguous", part, candidates=ids)
            continue
        pid = p["product_id"]
        kind = rng.randrange(4)
        if kind == 0:
            fact = {"product_id": pid, "field": "price_inr", "value": p["price_inr"]}
            add(rng.choice(PRICE_ASKS).format(name=name), "answer", "answer_lookup", part, [pid], [fact])
        elif kind == 1:
            fact = {"product_id": pid, "field": "stock_status", "value": p["stock_status"]}
            add(rng.choice(STOCK_ASKS).format(name=name), "answer", "answer_lookup", part, [pid], [fact])
        elif kind == 2 and "material" in p["attributes"]:
            fact = {"product_id": pid, "field": "material", "value": p["attributes"]["material"]}
            add(rng.choice(MATERIAL_ASKS).format(name=name), "answer", "answer_attribute", part, [pid], [fact])
        elif kind == 3 or kind == 2:
            attrs = p["attributes"]
            if "water_resistant" in attrs:
                fact = {"product_id": pid, "field": "water_resistant", "value": attrs["water_resistant"]}
                add(rng.choice(WATER_ASKS).format(name=name), "answer", "answer_attribute", part, [pid], [fact])
            elif "water_resistance_rating" not in attrs:
                add(rng.choice(WATER_ASKS).format(name=name), "refuse", "refuse_unanswerable", part, [pid])
    for (colour, sub), ids in _vague_groups(catalogue, rng):
        ask = rng.choice(VAGUE_ASKS).format(colour=colour, kind=sub.lower())
        add(ask, "clarify", "clarify_ambiguous", _part_of(colour + sub), candidates=ids)
    for item in NOT_SOLD:
        add(rng.choice(NOT_SOLD_ASKS).format(item=item), "refuse", "refuse_out_of_catalogue", _part_of(item))
    return rows


def _vague_groups(catalogue: list[dict], rng: random.Random, n: int = 16) -> list[tuple]:
    """Colour + sub-category pairs that match several products at different prices ("green running shoes")."""
    groups: dict[tuple, list[dict]] = {}
    for p in catalogue:
        colour = p["attributes"].get("colour")
        if colour and colour != "assorted":
            groups.setdefault((colour, p["sub_category"]), []).append(p)
    vague = [(k, [p["product_id"] for p in v]) for k, v in sorted(groups.items())
             if 2 <= len(v) <= 10 and len({p["price_inr"] for p in v}) > 1]  # fmt: skip
    return rng.sample(vague, min(n, len(vague)))


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The stress command's options: which part to score, and per-question details."""
    ap.add_argument("--part", choices=("tune", "holdout", "all"), default="tune")
    ap.add_argument("--details", action="store_true", help="also write per-question results (score_details.jsonl)")


def run(args: argparse.Namespace) -> None:
    """Build the stress questions from the pack's catalogue, answer them and score them with the official scorer."""
    _require_scorer_dependencies()
    paths = get_settings().paths
    catalogue = [json.loads(line) for line in paths.pack_catalogue.read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in build(catalogue) if args.part in ("all", r["part"])]
    out_dir = paths.reports_dir / "stress"
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = out_dir / f"stress_truth_{args.part}.jsonl"
    truth.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    questions = [Question(r["question_id"], r["conversation_id"], 1, r["question_text"]) for r in rows]
    assistant = build_assistant(paths.pack_catalogue, paths.pack_policy)
    predictions = write_predictions(predict(assistant, questions), out_dir / f"stress_predictions_{args.part}.jsonl")
    write_out(score_file(predictions, out_dir / f"stress_score_{args.part}.json", args.details, truth))

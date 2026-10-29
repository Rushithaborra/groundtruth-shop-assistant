"""A shopper-language set built from the pack's catalogue and policy headings (never from its dev or test questions),
scored by the official scorer.

    python main.py shoppers --part tune      # fix failures found here
    python main.py shoppers --part holdout   # measure only: never read its failures to fix them

Real shoppers in India write Hinglish and Telugu-English, use their own names for things ("tiffin", "mixie", "press"),
give budgets in many ways ("1.5k", "1500 lopu") and misspell product words. Each question applies one of those to
something the catalogue sells (or doesn't), and its expected behaviour is worked out by plain filtering of the
catalogue, never by asking the assistant.

The halves differ by **wording**, not only by product: every phrasing, shopper word and unsold item below is listed
in exactly one half, so fixing a tune word can't raise the holdout score. The holdout therefore measures how well
the assistant copes with words nobody tuned it for. Written by the same people as the code, so it is no substitute
for questions from real shoppers (the brief's Task 4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections.abc import Iterable

from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.console import write_out
from groundtruth.evaluation.official import _require_scorer_dependencies, score_file
from groundtruth.evaluation.questions import Question
from groundtruth.evaluation.submission import predict, write_predictions

from .stress import _identity

SEED = 2910

# --- wording, split by hand between the halves -----------------------------------------------------------------
BUDGET = {
    "tune": ("{kind} under {n}", "{kind} {n} ke andar", "{kind} below rs {n}", "{kind} {n} tak", "{kind} {n} se kam"),
    "holdout": (
        "{kind} {n} lopu",
        "{kind} {k}k lopala",
        "{kind} less than {n}",
        "{kind} {n} rupees max",
        "budget {n} {kind}",
        "{n} kanna takkuva {kind}",
    ),
}
CHEAPEST = {
    "tune": ("sabse sasta {kind}", "cheapest {kind}", "{kind} lowest price"),
    "holdout": ("takkuva dhara {kind}", "chavaka {kind}", "sasta wala {kind}", "kam daam wala {kind}"),
}
LOOKUP = {  # (wording, the fact it asks for)
    "tune": (
        ("{name} entha", "price_inr"),
        ("{name} dhara entha", "price_inr"),
        ("{name} ka daam kya hai", "price_inr"),
        ("{name} kitne ka padega", "price_inr"),
        ("{name} stock unda", "stock_status"),
    ),
    "holdout": (
        ("{name} rate entha andi", "price_inr"),
        ("{name} kitne mein milega", "price_inr"),
        ("{name} price cheppandi", "price_inr"),
        ("{name} available unda", "stock_status"),
        ("{name} undha stock lo", "stock_status"),
    ),
}
BROWSE = {"tune": ("{word} dikhao", "{word} chahiye"), "holdout": ("{word} chupinchu", "{word} kavali")}
# shoppers' own names for kinds the catalogue sells -> the catalogue's sub-category
OTHER_NAMES = {
    "tune": {
        "tiffin": "Lunch Boxes",
        "tiffin box": "Lunch Boxes",
        "mixie": "Mixer Grinders",
        "chadar": "Bedsheets",
        "lower": "Track Pants",
        "copy": "Notebooks",
        "sunblock": "Sunscreen",
        "deo": "Deodorants & Perfumes",
        "post it": "Sticky Notes",
        "compass box": "Geometry Boxes",
        "jump rope": "Skipping Ropes",
        "teddy": "Soft Toys",
        "tws": "Wireless Earbuds",
        "data cable": "USB Cables",
        "suitcase": "Trolley Bags",
        "sari": "Sarees",
        "kadhai": "Kadai & Woks",
        "fry pan": "Frying Pans",
        "phone stand": "Mobile Stands",
        "sipper": "Water Bottles",
    },
    "holdout": {
        "press": "Irons",
        "iron box": "Irons",
        "parda": "Curtains",
        "tauliya": "Towels",
        "moisturiser": "Body Lotion",
        "perfume": "Deodorants & Perfumes",
        "chapstick": "Lip Balm",
        "rc car": "Remote Control Cars",
        "jigsaw": "Jigsaw Puzzles",
        "charging cable": "USB Cables",
        "adapter": "Chargers",
        "gym bag": "Duffel Bags",
        "luggage bag": "Trolley Bags",
        "hawai chappal": "Flip-Flops & Slippers",
        "mojari": "Ethnic Footwear",
        "racquet": "Badminton Rackets",
        "exercise mat": "Yoga Mats",
        "sandwich toaster": "Sandwich Makers",
        "hand mixer": "Hand Blenders",
        "beard trimmer": "Trimmers",
    },
}
POLICY = {  # (wording, the policy sub-section that answers it, read from the policy's own headings)
    "tune": (
        ("return kitne din mein kar sakte hai", "1.1"),
        ("refund kab tak aayega", "1.5"),
        ("exchange kar sakte hai kya", "1.6"),
        ("delivery charge kitna lagta hai", "2.1"),
        ("delivery kitne din mein hogi", "2.2"),
        ("cash on delivery milega kya", "4.2"),
        ("order cancel kaise karu dispatch se pehle", "5.1"),
        ("warranty milegi kya", "3.1"),
    ),
    "holdout": (
        ("return cheyyadaniki enni rojulu untayi", "1.1"),
        ("refund eppudu vastundi", "1.5"),
        ("size exchange cheyyocha", "1.6"),
        ("delivery ki entha charge chestaru", "2.1"),
        ("delivery ki enni rojulu padutundi", "2.2"),
        ("COD available aa", "4.2"),
        ("dispatch kakamundu order cancel cheyyocha", "5.1"),
        ("warranty untunda", "3.1"),
    ),
}
NOT_SOLD = {
    "tune": ("fridge", "washing machine", "mobile phone", "laptop", "atta", "rice", "medicine", "sofa"),
    "holdout": ("geyser", "television", "dining table", "cooking oil", "gold ring", "bicycle", "mattress", "printer"),
}
NOT_SOLD_ASKS = {"tune": ("{item} hai kya", "{item} dikhao"), "holdout": ("{item} unda", "{item} kavali")}


def _hashed_part(key: str) -> str:
    """Assign a question to the tune or holdout part by hashing its key, so the split is stable."""
    return "tune" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "holdout"


def _kinds(catalogue: list[dict]) -> dict[str, list[dict]]:
    """Sub-categories a shopper can name as written ("pressure cookers"), with their products."""
    kinds: dict[str, list[dict]] = {}
    for p in catalogue:
        if "&" not in p["sub_category"]:
            kinds.setdefault(p["sub_category"], []).append(p)
    return kinds


def _typo(word: str, rng: random.Random) -> str:
    """One slip in the longest word of a product name: a dropped, doubled or swapped letter ("runing shoes")."""
    words = word.split()
    i = max(range(len(words)), key=lambda j: len(words[j]))
    w = words[i]
    if len(w) < 5:
        return word
    k = rng.randrange(1, len(w) - 2)
    w = rng.choice((w[:k] + w[k + 1 :], w[:k] + w[k] + w[k:], w[:k] + w[k + 1] + w[k] + w[k + 2 :]))
    return " ".join(words[:i] + [w] + words[i + 1 :])


def _budget(products: list[dict]) -> int | None:
    """A round budget that some, but not all, of these products fit (about the 40th price percentile)."""
    prices = sorted(p["price_inr"] for p in products)
    n = int(math.ceil(prices[int(len(prices) * 0.4)] / 100.0) * 100)
    fits = sum(p <= n for p in prices)
    return n if 2 <= fits < len(prices) else None


class _Rows(list):
    """Truth rows in the official scorer's format."""

    def add(
        self,
        text: str,
        action: str,
        qtype: str,
        part: str,
        products: Iterable[str] = (),
        facts: Iterable[dict] = (),
        sections: Iterable[str] = (),
    ) -> None:
        """Add one question with its expected action and the products, facts and sections that answer it."""
        self.append(
            {
                "question_id": f"SH-{len(self):04d}",
                "conversation_id": f"SHC-{len(self):04d}",
                "turn": 1,
                "question_text": text,
                "expected_action": action,
                "question_type": qtype,
                "part": part,
                "acceptable_product_ids": list(products),
                "required_facts": list(facts),
                "clarify_candidate_ids": [],
                "policy_sections": list(sections),
            }
        )


def build(catalogue: list[dict], seed: int = SEED) -> list[dict]:
    """Truth rows for shopper-language questions about the catalogue and policy, each tagged tune or holdout."""
    rng = random.Random(seed)
    kinds = _kinds(catalogue)
    rows = _Rows()
    for sub in sorted(kinds):
        _kind_questions(rows, sub.lower(), kinds[sub], rng)
    for part, names in OTHER_NAMES.items():
        for word, sub in names.items():
            ids = [p["product_id"] for p in catalogue if p["sub_category"] == sub]
            rows.add(rng.choice(BROWSE[part]).format(word=word), "answer", "answer_filter", part, ids)
    _lookup_questions(rows, catalogue, rng)
    for part, asks in POLICY.items():
        for text, section in asks:
            rows.add(text, "answer", "answer_policy", part, sections=[section])
    for part, items in NOT_SOLD.items():
        for item in items:
            rows.add(rng.choice(NOT_SOLD_ASKS[part]).format(item=item), "refuse", "refuse_out_of_catalogue", part)
    return list(rows)


def _kind_questions(rows: _Rows, kind: str, products: list[dict], rng: random.Random) -> None:
    """A budget listing, a cheapest-one question and a misspelt listing for one kind of product."""
    n = _budget(products)
    if n is not None:
        part = rng.choice(("tune", "holdout"))
        asks = [a for a in BUDGET[part] if n >= 1000 or "{k}" not in a]  # "1.5k" yes, "0.8k" no
        text = rng.choice(asks).format(kind=kind, n=n, k=f"{n / 1000:g}")
        ids = [p["product_id"] for p in products if p["price_inr"] <= n]
        rows.add(text, "answer", "answer_filter", part, ids)
    low = min(p["price_inr"] for p in products)
    cheapest = [p for p in products if p["price_inr"] == low]
    part = rng.choice(("tune", "holdout"))
    facts = (
        [{"product_id": cheapest[0]["product_id"], "field": "price_inr", "value": low}] if len(cheapest) == 1 else []
    )
    rows.add(
        rng.choice(CHEAPEST[part]).format(kind=kind),
        "answer",
        "answer_superlative",
        part,
        [p["product_id"] for p in cheapest],
        facts,
    )
    misspelt = _typo(kind, rng)
    if misspelt != kind:
        ids = [p["product_id"] for p in products]
        rows.add(f"{misspelt} dikhao", "answer", "answer_filter", _hashed_part(kind), ids)


def _lookup_questions(rows: _Rows, catalogue: list[dict], rng: random.Random, n: int = 60) -> None:
    """Price and stock questions about products named exactly, in Hinglish and Telugu-English."""
    by_identity: dict[tuple, list[dict]] = {}
    for p in catalogue:
        by_identity.setdefault(_identity(p), []).append(p)
    unique = [p for p in catalogue if len(by_identity[_identity(p)]) == 1]
    for p in rng.sample(unique, min(n, len(unique))):
        brand, line, colour, sub = _identity(p)
        name = " ".join(x for x in (brand, line, colour, sub.lower()) if x)
        part = rng.choice(("tune", "holdout"))
        text, field = rng.choice(LOOKUP[part])
        fact = {"product_id": p["product_id"], "field": field, "value": p[field]}
        rows.add(text.format(name=name), "answer", "answer_lookup", part, [p["product_id"]], [fact])


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The shoppers command's options: which part to score, and per-question details."""
    ap.add_argument("--part", choices=("tune", "holdout", "all"), default="tune")
    ap.add_argument("--details", action="store_true", help="also write per-question results (score_details.jsonl)")


def run(args: argparse.Namespace) -> None:
    """Build the shopper-language questions, answer them and score them with the official scorer."""
    _require_scorer_dependencies()
    paths = get_settings().paths
    catalogue = [json.loads(line) for line in paths.pack_catalogue.read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in build(catalogue) if args.part in ("all", r["part"])]
    out_dir = paths.reports_dir / "shoppers"
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = out_dir / f"shoppers_truth_{args.part}.jsonl"
    truth.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    questions = [Question(r["question_id"], r["conversation_id"], 1, r["question_text"]) for r in rows]
    assistant = build_assistant(paths.pack_catalogue, paths.pack_policy)
    predictions = write_predictions(predict(assistant, questions), out_dir / f"shoppers_predictions_{args.part}.jsonl")
    write_out(score_file(predictions, out_dir / f"shoppers_score_{args.part}.json", args.details, truth))

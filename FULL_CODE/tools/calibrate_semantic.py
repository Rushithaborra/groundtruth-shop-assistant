"""Pick the embedding-fallback thresholds from labelled examples, using the provided embedding model.

    python main.py calibrate --embed-model PATH/TO/MODEL [--catalogue ...] [--policy ...]

Writes semantic_config.json, which build_assistant needs before the fallback switches on.
Rule: map_threshold sits just above the best score of any word the shop does NOT sell, so with this model none of
those words can be mapped onto a product type. That gives zero false mappings on the list below, and the script
reports how many true synonyms still clear it. Extend both lists with words from the real catalogue and queries,
then re-run. Read the printed table: it is the evidence for the numbers, not just the numbers.
"""
from __future__ import annotations

import argparse
import json
import sys

from groundtruth import CatalogueIndex, load_catalogue, load_policy
from groundtruth.models.local import LocalEmbedder
from groundtruth.models.semantic import SemanticFallback, dot
from groundtruth.paths import DATA_DIR
from groundtruth.text import tokenize

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# shopper word -> acceptable catalogue type words (tokenised form). Words missing from the catalogue are skipped.
SHOULD_MAP = {
    "trainers": {"shoe", "running"}, "sneakers": {"shoe", "running"}, "jogging": {"running", "shoe"},
    "earphones": {"earbud"}, "tws": {"earbud", "wireless"}, "fitness band": {"smartwatch"},
    "portable charger": {"power", "bank"}, "flask": {"bottle", "water"}, "rucksack": {"backpack", "bag"},
    "purse": {"wallet"}, "bedcover": {"bedsheet"}, "tee": {"tshirt"}, "sweatshirt": {"hoodie"},
    "slippers": {"sandal"}, "flip flops": {"sandal"}, "denims": {"jean"}, "trousers": {"jean"},
    "kurti": {"kurta"}, "boombox": {"speaker", "bluetooth"}, "tea kettle": {"kettle", "electric"},
}
# Things the shop doesn't sell: none may be mapped. (Close relatives like headphones may become *alternatives*.)
MUST_NOT_MAP = """iphone phone mobile laptop saree lehenga tv television refrigerator fridge sunglasses perfume lipstick
book mattress sofa mouse keyboard camera bicycle toy tablet headphones jacket blazer cap umbrella""".split()
POLICY_POS = {"paisa wapas kab aayega": "returns", "money back timeline": "returns", "free delivery hai": "shipping",
              "can i swap for another size": "exchanges", "stop my order": "cancellations",
              "guarantee on electronics": "warranty"}
POLICY_NEG = ["do you have a store in hyderabad", "can i pay with paytm", "emi option", "gift wrapping",
              "who is the ceo", "loyalty points"]


def calibrate(fallback: SemanticFallback, margin: float = 0.02) -> dict:
    targets = set(fallback.targets)
    rows = []
    for word in MUST_NOT_MAP:
        m = fallback.nearest_type(" ".join(tokenize(word)) or word)
        rows.append(("refuse", word, m.target, m.score, None))
    neg_max = max(r[3] for r in rows)
    map_t = round(neg_max + margin, 4)
    for word, ok in SHOULD_MAP.items():
        if not ok & targets:
            continue
        m = fallback.nearest_type(" ".join(tokenize(word)) or word)
        rows.append(("map", word, m.target, m.score, m.target in ok))
    alt_t = round(map_t - 0.08, 4)
    policy_t = None
    pos, neg = [], []
    if fallback.policy_units:
        sec_by_sent = [s.id for s, _ in fallback.policy_units]
        for q, sec in POLICY_POS.items():
            v = fallback.embedder.encode([q])[0]
            score, i = max((dot(v, p), i) for i, p in enumerate(fallback.policy_vecs))
            pos.append((q, sec_by_sent[i], score, sec_by_sent[i] == sec))
        for q in POLICY_NEG:
            v = fallback.embedder.encode([q])[0]
            neg.append((q, max(dot(v, p) for p in fallback.policy_vecs)))
        policy_t = round(max(s for _, s in neg) + margin, 4)
    mapped_ok = [r for r in rows if r[0] == "map" and r[4] and r[3] >= map_t]
    report = {"map_threshold": map_t, "alt_threshold": alt_t, "policy_threshold": policy_t,
              "should_map_recall": round(len(mapped_ok) / max(1, sum(r[0] == "map" for r in rows)), 3),
              "wrong_target_above_threshold": [r[1] for r in rows if r[0] == "map" and not r[4] and r[3] >= map_t],
              "policy_recall": round(sum(ok and s >= (policy_t or 9) for _, _, s, ok in pos) / max(1, len(pos)), 3),
              "model": fallback.embedder.name}
    return {"config": report, "rows": rows, "policy_pos": pos, "policy_neg": neg}


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--embed-model", required=True)
    ap.add_argument("--catalogue", default=str(DATA_DIR / "catalogue.jsonl"))
    ap.add_argument("--policy", default=str(DATA_DIR / "policy.txt"))
    ap.add_argument("--out", default="semantic_config.json")


def run(args: argparse.Namespace) -> None:
    index, policy = CatalogueIndex(load_catalogue(args.catalogue)), load_policy(args.policy)
    fb = SemanticFallback(index, policy, LocalEmbedder(args.embed_model), map_threshold=1.0)
    result = calibrate(fb)
    print(f"{'kind':7} {'word':18} {'nearest':12} {'score':>6}  ok")
    for kind, word, target, score, ok in sorted(result["rows"], key=lambda r: -r[3]):
        print(f"{kind:7} {word:18} {target:12} {score:6.3f}  {'' if ok is None else ok}")
    print("\npolicy positives:")
    for q, sec, s, ok in result["policy_pos"]:
        print(f"  {s:6.3f} {sec:14} {'ok' if ok else 'WRONG SECTION'}  {q}")
    print("policy negatives (must stay below threshold):")
    for q, s in result["policy_neg"]:
        print(f"  {s:6.3f} {q}")
    cfg = result["config"]
    print("\n" + json.dumps(cfg, indent=2))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    print(f"\nwrote {args.out}. If should_map_recall is low, the model can't separate synonyms from unsold items at "
          "this margin: keep the fallback off rather than lowering map_threshold by hand.")

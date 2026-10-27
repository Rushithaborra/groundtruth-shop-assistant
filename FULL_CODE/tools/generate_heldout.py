"""Held-out evaluation questions: written after the first round of fixes and NEVER used for tuning.

Its score is the only one in this repo that estimates how the assistant generalises to unseen phrasing.
If you fix a failure here, the set stops being held out: write a fresh one.

    python data/generate_scale.py && python data/generate_heldout.py
    python evaluate.py --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt \
                       --questions data/scale/heldout_questions.jsonl --verbose
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.generate_independent import EAR, PRODUCTS, Q, RUN, SCALE, WATCH, clarify, listing, pick, policy, refuse, single

Q.clear()
listing("h01", ["trainers for running"], pick(RUN))
listing("h02", ["gents ke liye joote under 2k"], pick(category="Footwear", gender=["men", "unisex"], max_price=2000))
listing("h03", ["need a fitness band"], pick(WATCH), accept=("answer", "refuse"))
listing("h04", ["tws earbuds below 1500"], pick(EAR, max_price=1500), accept=("answer", "refuse"))
listing("h05", ["linen shirt for office"], pick({"Shirt"}, material="linen"))
listing("h06", ["blue jeans 30 waist"], pick({"Jeans"}, color="blue", size="30"), accept=("answer", "refuse"))
listing("h07", ["green hoodie XL"], pick({"Hoodie"}, color="green", size="XL"))
listing("h08", ["single bed sheet"], pick({"Bedsheet"}, size="single"))
listing("h09", ["portable charger"], pick({"Power Bank"}), accept=("answer", "refuse"))
listing("h10", ["speaker for party under 3000"], pick({"Bluetooth Speaker"}, max_price=3000))
listing("h11", ["school bag"], pick({"Backpack"}), accept=("answer", "refuse"))
listing("h12", ["kettle for tea"], pick({"Electric Kettle"}))
listing("h13", ["purse for women"], pick({"Wallet"}, gender=["women", "unisex"]), accept=("answer", "refuse"))
listing("h14", ["white sneakers womens size 7"], pick(RUN, color="white", gender=["women", "unisex"], size="7"))
listing("h15", ["sabse acchi rating wali smartwatch"], [p for p in pick(WATCH)
                                                        if (p.rating or 0) >= max(x.rating or 0 for x in pick(WATCH)) - 0.1])
listing("h16", ["polyester t-shirt between 500 and 1000"], pick({"T-Shirt"}, material="polyester", min_price=500,
                                                                  max_price=1000))
listing("h17", ["sandals", "under 800", "black"], pick({"Sandals"}, max_price=800, color="black"))
listing("h18", ["earbuds", "not black"], [p for p in pick(EAR) if "black" not in [v.lower() for v in p.attr_values("color")]])
w = next(p for p in PRODUCTS if "Wallet" in p.title)
single("h19", [f"{w.title} ka rating kitna hai"], w, must=["rated"])
clarify("h20", ["what's the price of the white sandals"])
clarify("h21", ["green kettle kitne ka hai"])
clarify("h22", ["the leather wallet price"])
refuse("h23", ["oneplus nord"])
refuse("h24", ["sunglasses"])
refuse("h25", ["perfume for men"])
refuse("h26", ["washing machine"])
refuse("h27", ["adidas hoodie"])
refuse("h28", ["smartwatch under 100"])
refuse("h29", ["do you sell books"])
refuse("h30", ["mattress king size"])
policy("h31", ["paisa wapas kab aayega"], ["returns"], must=["7 business days"])
policy("h32", ["free delivery hai?"], ["shipping"], must=["999"])
policy("h33", ["can i cancel after dispatch"], ["cancellations"], must=["dispatched"])
policy("h34", ["do shoes have warranty"], ["warranty"], must=["no warranty"])
policy("h35", ["return window kitne din ka hai"], ["returns"], must=["30 days"])
refuse("h36", ["do you have a store in hyderabad"])
refuse("h37", ["can i pay with paytm"])

def main() -> None:
    out = SCALE / "heldout_questions.jsonl"
    out.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in Q) + "\n", encoding="utf-8")
    print(f"wrote {len(Q)} questions to {out}")


if __name__ == "__main__":
    main()

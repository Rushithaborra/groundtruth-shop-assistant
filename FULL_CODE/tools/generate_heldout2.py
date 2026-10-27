"""Held-out set 2: written after round-3 fixes and NEVER used for tuning.

data/generate_heldout.py became a regression set once its three failures were fixed; this set replaces it as the
generalisation estimate. If you fix a failure found here, this set stops being held out: write a new one.

    python data/generate_scale.py && python data/generate_heldout2.py
    python evaluate.py --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt \
                       --questions data/scale/heldout2_questions.jsonl --verbose
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.generate_independent import EAR, PRODUCTS, Q, RUN, SCALE, WATCH, clarify, listing, pick, policy, refuse, single

Q.clear()
listing("k01", ["need joggers shoes 2500 se kam"], pick(RUN, max_price=2500), accept=("answer", "refuse"))
listing("k02", ["ladies sandal under 700"], pick({"Sandals"}, gender=["women", "unisex"], max_price=700))
listing("k03", ["mens cotton shirt"], pick({"Shirt"}, material="cotton", gender=["men", "unisex"]))
listing("k04", ["hoodie in green colour"], pick({"Hoodie"}, color="green"))
listing("k05", ["steel bottle"], pick({"Water Bottle"}, material="steel"))
listing("k06", ["earbuds 1500 tak"], pick(EAR, max_price=1500))
listing("k07", ["denim jeans 32"], pick({"Jeans"}, material="denim", size="32"), accept=("answer", "refuse"))
listing("k08", ["queen size bedsheet"], pick({"Bedsheet"}, size="queen"))
listing("k09", ["kettle for office"], pick({"Electric Kettle"}))
listing("k10", ["portable speaker below 2500"], pick({"Bluetooth Speaker"}, max_price=2500))
listing("k11", ["white tshirt for women size S"], pick({"T-Shirt"}, color="white", gender=["women", "unisex"], size="S"))
listing("k12", ["power bank sabse sasta"], pick({"Power Bank"}))
listing("k13", ["smartwatch between 3000 and 6000"], pick(WATCH, min_price=3000, max_price=6000))
listing("k14", ["leather wallet below 1000"], pick({"Wallet"}, material="leather", max_price=1000))
listing("k15", ["black backpack"], pick({"Backpack"}, color="black"))
listing("k16", ["kurta", "only red ones"], pick({"Kurta"}, color="red"))
listing("k17", ["jeans", "for men", "under 1800"], pick({"Jeans"}, gender=["men", "unisex"], max_price=1800))
listing("k18", ["sandals", "not white"], [p for p in pick({"Sandals"}) if "white" not in [v.lower() for v in p.attr_values("color")]])
listing("k19", ["speakers", "highest rated"], [p for p in pick({"Bluetooth Speaker"})
                                                if (p.rating or 0) >= max(x.rating or 0 for x in pick({"Bluetooth Speaker"})) - 0.1])
w = next(p for p in PRODUCTS if "Bedsheet" in p.title)
single("k20", [f"how much does {w.title} cost"], w, must=[f"{w.price:,.0f}"])
s = next(p for p in PRODUCTS if "Smartwatch" in p.title and p.in_stock)
single("k21", [f"{s.id} stock hai?"], s, must=["in stock"])
clarify("k22", ["how much is the red tshirt"])
clarify("k23", ["the blue jeans kitne ki hai"])
clarify("k24", ["is the white kettle in stock"])
clarify("k25", ["what about the price"])
refuse("k26", ["do you sell laptops"])
refuse("k27", ["apple watch"])
refuse("k28", ["perfumes"])
refuse("k29", ["jbl speaker"])
refuse("k30", ["kurta under 50"])
refuse("k31", ["sofa set"])
refuse("k32", ["puma shoes"])
refuse("k33", ["wireless mouse"])
refuse("k34", ["where is my parcel"])
refuse("k35", ["any cashback offers"])
policy("k36", ["return kar sakte hai kya"], ["returns"], must=["30 days"])
policy("k37", ["free shipping kab milta hai"], ["shipping"], must=["999"])
policy("k38", ["can i get refund in cash"], ["returns"], must=["original payment method"])
policy("k39", ["can opened earphones be returned"], ["returns"], must=["cannot be returned once opened"])
policy("k40", ["exchange for bigger size"], ["exchanges"], must=["15 days"])
policy("k41", ["cancel after shipping"], ["cancellations"], must=["dispatched"])
policy("k42", ["is cod available"], ["shipping"], must=["cash on delivery"])
policy("k43", ["warranty for speaker"], ["warranty"])
refuse("k44", ["do you ship to nepal"])  # deliberately labelled refuse? No: the policy answers it
Q[-1] = {"id": "k44", "turns": ["do you ship to nepal"], "category": "policy",
         "expected": {"behaviour": "answer", "policy_sections": ["shipping"], "must_include": ["outside india"]}}
refuse("k45", ["installation service milega"])

def main() -> None:
    out = SCALE / "heldout2_questions.jsonl"
    out.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in Q) + "\n", encoding="utf-8")
    print(f"wrote {len(Q)} questions to {out}")


if __name__ == "__main__":
    main()

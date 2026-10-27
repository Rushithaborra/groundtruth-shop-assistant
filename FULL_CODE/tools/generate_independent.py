"""Independent evaluation questions for the 5,000-product scale catalogue.

The query wording below was written from a shopper's point of view BEFORE any tuning, and must not be edited to
make the assistant pass. Expected product ids are derived here by plain filtering of the catalogue (never by
calling the assistant), so the ground truth is independent of the code under test.

    python data/generate_scale.py          # creates data/scale/catalogue.jsonl
    python data/generate_independent.py    # writes data/scale/independent_questions.jsonl
    python evaluate.py --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt \
                       --questions data/scale/independent_questions.jsonl --verbose
"""
from __future__ import annotations

import json
from pathlib import Path

from groundtruth.catalogue import load_catalogue  # field-name normalisation only
from groundtruth.paths import SCALE_DIR as SCALE
PRODUCTS = load_catalogue(SCALE / "catalogue.jsonl")


def ptype(p) -> str:
    return p.title.split("(")[0].strip().split(" ", 3)[-1]  # "Brand Model N Running Shoes (Blue)" -> "Running Shoes"


def attr(p, key) -> list[str]:
    return [v.strip().lower() for v in p.attr_values(key)]


def pick(types=None, max_price=None, min_price=None, color=None, material=None, gender=None, size=None,
         category=None, brand=None):
    out = []
    for p in PRODUCTS:
        if types and ptype(p) not in types:
            continue
        if category and p.category != category:
            continue
        if max_price is not None and p.price > max_price:
            continue
        if min_price is not None and p.price < min_price:
            continue
        if color and color not in attr(p, "color"):
            continue
        if material and material not in attr(p, "material"):
            continue
        if gender and not set(attr(p, "gender")) & set(gender):
            continue
        if size and size.lower() not in attr(p, "size"):
            continue
        if brand and brand.lower() not in attr(p, "brand"):
            continue
        out.append(p)
    return out


def ids(ps):
    return [p.id for p in ps]


Q: list[dict] = []


def listing(qid, turns, ps, accept=("answer",)):
    behaviour = list(accept) if ps else ["refuse"]
    exp = {"behaviour": behaviour if len(behaviour) > 1 else behaviour[0]}
    if ps:
        exp["valid_ids"] = ids(ps)
    Q.append({"id": qid, "turns": turns, "category": "answerable", "expected": exp})


def single(qid, turns, p, must=()):
    Q.append({"id": qid, "turns": turns, "category": "answerable",
              "expected": {"behaviour": "answer", "product_ids": [p.id], "must_include": list(must)}})


def clarify(qid, turns):
    Q.append({"id": qid, "turns": turns, "category": "ambiguous", "expected": {"behaviour": "clarify"}})


def refuse(qid, turns):
    Q.append({"id": qid, "turns": turns, "category": "out_of_catalogue", "expected": {"behaviour": "refuse"}})


def policy(qid, turns, sections, must=()):
    Q.append({"id": qid, "turns": turns, "category": "policy",
              "expected": {"behaviour": "answer", "policy_sections": sections, "must_include": list(must)}})


RUN, EAR, WATCH = {"Running Shoes"}, {"Wireless Earbuds"}, {"Smartwatch"}

# ---- answerable, paraphrased / code-mixed ----------------------------------------------------------------
listing("i01", ["shoes for jogging under 3k"], pick(RUN, max_price=3000))
listing("i02", ["sneakers for gym"], pick(RUN))
listing("i03", ["kuch sasta earbuds dikhao"], pick(EAR))
listing("i04", ["is there any waterproof watch"], pick(WATCH), accept=("answer", "refuse"))
listing("i05", ["smart watch under 5000"], pick(WATCH, max_price=5000))
listing("i06", ["black tshirt medium size"], pick({"T-Shirt"}, color="black", size="M"))
listing("i07", ["M size black t shirt"], pick({"T-Shirt"}, color="black", size="M"))
listing("i08", ["cotton kurta 1500 ke andar"], pick({"Kurta"}, material="cotton", max_price=1500))
listing("i09", ["ladies kurta red"], pick({"Kurta"}, color="red", gender=["women", "unisex"]))
listing("i10", ["denim jeans for women under 2000"], pick({"Jeans"}, material="denim", gender=["women", "unisex"],
                                                         max_price=2000))
listing("i11", ["hoodie for winter"], pick({"Hoodie"}))
listing("i12", ["bedsheet king size"], pick({"Bedsheet"}, size="king"))
listing("i13", ["double bedsheet cotton"], pick({"Bedsheet"}, size="double", material="cotton"))
listing("i14", ["electric kettle steel"], pick({"Electric Kettle"}, material="steel"))
listing("i15", ["kettle under 1000"], pick({"Electric Kettle"}, max_price=1000))
listing("i16", ["power bank 20000mah"], pick({"Power Bank"}), accept=("answer", "refuse"))
listing("i17", ["powerbank sasta wala"], pick({"Power Bank"}))
listing("i18", ["speaker bluetooth blue colour"], pick({"Bluetooth Speaker"}, color="blue"))
listing("i19", ["water bottle steel 1 litre"], pick({"Water Bottle"}, material="steel"), accept=("answer", "refuse"))
listing("i20", ["leather wallet for men"], pick({"Wallet"}, material="leather", gender=["men", "unisex"]))
listing("i21", ["backpack for laptop"], pick({"Backpack"}))
listing("i22", ["sandals for men under 1000"], pick({"Sandals"}, gender=["men", "unisex"], max_price=1000))
listing("i23", ["chappal dikhao"], pick({"Sandals"}))
listing("i24", ["joote size 9"], pick(category="Footwear", size="9"))
ear = pick(EAR)
top = max(p.rating or 0 for p in ear)
listing("i25", ["highest rated earbuds"], [p for p in ear if (p.rating or 0) >= top - 0.1])

# ---- multi-turn ----------------------------------------------------------------------------------------
listing("i26", ["cotton shirt chahiye", "under 1500", "blue wala"],
        pick({"Shirt"}, material="cotton", max_price=1500, color="blue"))
listing("i27", ["running shoes", "size 8", "actually under 2500"], pick(RUN, size="8", max_price=2500))
listing("i28", ["cotton kurta", "under ₹2,000", "red"], pick({"Kurta"}, material="cotton", max_price=2000, color="red"))
cheapest = min(ear, key=lambda p: (p.price, -(p.rating or 0), p.id))
single("i29", ["earbuds dikhao", "cheapest one ka price kya hai"], cheapest, must=[f"{cheapest.price:,.0f}"])
some = next(p for p in PRODUCTS if ptype(p) == "Hoodie")
single("i30", [f"price of {some.title}", "is it in stock?"], some, must=["stock"])

# ---- ambiguous ----------------------------------------------------------------------------------------
clarify("i31", ["price of the blue running shoe"])
clarify("i32", ["how much is the black hoodie"])
clarify("i33", ["red kurta ka price"])
clarify("i34", ["is the grey backpack in stock"])
clarify("i35", ["price?"])
clarify("i36", ["is it in stock"])

# ---- out of catalogue ---------------------------------------------------------------------------------
for qid, q in [("i37", "do you sell iPhones?"), ("i38", "samsung galaxy price"), ("i39", "red saree"),
               ("i40", "laptop under 50000"), ("i41", "nike running shoes"), ("i42", "headphones"),
               ("i43", "gaming mouse"), ("i44", "lipstick shades"), ("i45", "running shoes under 200"),
               ("i46", "do u have refrigerator"), ("i47", "cotton tshirt under ₹100"),
               ("i48", "what's the warranty on samsung tv")]:
    refuse(qid, [q])

# ---- policy -------------------------------------------------------------------------------------------
policy("i49", ["how long does refund take"], ["returns"], must=["7 business days"])
policy("i50", ["refund kab milega"], ["returns"], must=["7 business days"])
policy("i51", ["delivery to dubai?"], ["shipping"], must=["outside india"])
policy("i52", ["COD hai kya"], ["shipping"], must=["cash on delivery"])
policy("i53", ["shipping charges kitne hai"], ["shipping"], must=["₹49"])
policy("i54", ["size galat aaya toh exchange ho jayega?"], ["exchanges"], must=["15 days"])
policy("i55", ["order cancel kaise kare"], ["cancellations"], must=["dispatched"])
policy("i56", ["can i return opened earbuds"], ["returns"], must=["cannot be returned once opened"])
policy("i57", ["kitne din mein delivery hogi"], ["shipping"], must=["3 to 5 business days"])
policy("i58", ["warranty on smartwatch"], ["warranty"])
refuse("i59", ["do you give EMI option"])
refuse("i60", ["gift wrapping available?"])

def main() -> None:
    out = SCALE / "independent_questions.jsonl"
    out.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in Q) + "\n", encoding="utf-8")
    print(f"wrote {len(Q)} questions to {out}")


if __name__ == "__main__":
    main()

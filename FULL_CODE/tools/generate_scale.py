"""Generate a 5,000-product catalogue with realistic mess, to stress-test the assistant at the real size.

Run:  python data/generate_scale.py     (writes data/scale/*, which is git-ignored)

Differences from the small synthetic set: colour appears in titles, brands/models come from large pools, and about a
tenth of products have messy attributes (alternative key spelling, odd casing, trailing spaces, missing fields).
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import generate_synthetic as small

OUT = Path(__file__).parent / "scale"
N_PRODUCTS = 5000
rng = random.Random(11)

SYLLABLES = ["ar", "ben", "cor", "del", "eni", "fa", "gor", "hel", "ivo", "jun", "kor", "lum", "mar", "nex", "ori",
             "pra", "quo", "rin", "sol", "tav", "uma", "vex", "wyn", "xel", "yor", "zan"]


def brand_pool(n: int) -> list[str]:
    names = set()
    while len(names) < n:
        names.add("".join(rng.choice(SYLLABLES) for _ in range(rng.choice([2, 3]))).capitalize())
    return sorted(names)


BRANDS = brand_pool(60)
MODEL_WORDS = small.MODELS + ["Aero", "Bolt", "Cruz", "Drift", "Edge", "Flare", "Grove", "Haze", "Iris", "Jet",
                              "Kite", "Lark", "Mist", "Noble", "Onyx", "Prime", "Quill", "Ridge", "Sage", "Tide"]


def make_scale_catalogue() -> list[dict]:
    products, used = [], set()
    types = small.TYPES
    while len(products) < N_PRODUCTS:
        t = types[len(products) % len(types)]
        brand, model = rng.choice(BRANDS), f"{rng.choice(MODEL_WORDS)} {rng.randint(1, 9)}"
        material = rng.choice(t["materials"])
        color = rng.choice(small.COLORS)
        title = f"{brand} {model} {t['name']} ({color.title()})"
        if title in used:
            continue
        used.add(title)
        attrs = {"brand": brand, "color": color, "material": material}
        if t["gender"]:
            attrs["gender"] = rng.choice(t["gender"])
        if t["sizes"]:
            attrs["size"] = t["sizes"]
        if t.get("warranty"):
            attrs["warranty"] = t["warranty"]
        if rng.random() < 0.10:  # messy
            roll = rng.random()
            if roll < 0.4:
                attrs["Colour"] = attrs.pop("color").upper() + " "
            elif roll < 0.7:
                attrs["material"] = attrs["material"].upper()
            else:
                attrs.pop("material")
        lo, hi = t["price"]
        products.append({
            "id": f"P{len(products) + 1:05d}",
            "title": title,
            "description": t["desc"].format(material=material),
            "category": t["category"],
            "attributes": attrs,
            "price": int(round(rng.uniform(lo, hi), -1)) - 1,
            "in_stock": rng.random() > 0.15,
            "rating": round(rng.uniform(3.0, 4.9), 1),
            "rating_count": rng.randint(3, 5000),
        })
    return products


def main() -> None:
    OUT.mkdir(exist_ok=True)
    products = make_scale_catalogue()
    (OUT / "catalogue.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in products) + "\n",
                                         encoding="utf-8")
    shutil.copy(Path(__file__).parent / "policy.txt", OUT / "policy.txt")
    # the same question templates as the small set; expectations are recomputed against this catalogue
    # (messy attribute spellings are normalised here the way a careful reader would, so expectations stay fair)
    clean = [dict(p, attributes={k.lower().replace("colour", "color"): (v.strip().lower() if k.lower() in {"colour", "color", "material"} else v)
                                 for k, v in p["attributes"].items()}) for p in products]
    qs = small.make_questions(clean)
    (OUT / "test_questions.jsonl").write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in qs) + "\n",
                                              encoding="utf-8")
    print(f"wrote {len(products)} products, {len(qs)} questions to {OUT}")


if __name__ == "__main__":
    main()

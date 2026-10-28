"""Generate a small synthetic catalogue, policy text and test questions (stand-ins for the real files).

Run:  python data/generate_synthetic.py
Test expectations are computed here by brute-force filtering over the catalogue, independently of the assistant.
The question set is a smoke test written alongside the assistant, not a benchmark: use the real 150 questions
for any real measurement.
"""

from __future__ import annotations

import json
import random

from groundtruth.config import get_settings
from groundtruth.console import write_out

OUT = get_settings().paths.data_dir
rng = random.Random(7)

COLORS = ["black", "white", "blue", "red", "grey", "green"]
SPORT = ["Stridex", "Velora", "Aerotrek", "Kicksy"]
APPAREL = ["Threadly", "Lomax", "Wearwell", "Indigo Lane"]
TECH = ["Zenbyte", "Pulsar", "Voltix", "Soundora"]
HOME = ["Homebase", "Casa Nova", "Kettlecraft", "Everfresh"]
MODELS = ["Pulse", "Nova", "Breeze", "Apex", "Glide", "Orbit", "Flex", "Echo", "Zephyr", "Ember", "Terra", "Lumen"]

TYPES = [
    dict(
        name="Running Shoes",
        category="Footwear",
        brands=SPORT,
        price=(1299, 5999),
        materials=["mesh", "knit"],
        gender=["men", "women", "unisex"],
        sizes=["6", "7", "8", "9", "10"],
        n=9,
        desc="Lightweight running shoes with a cushioned sole and a breathable {material} upper.",
    ),
    dict(
        name="Sandals",
        category="Footwear",
        brands=SPORT,
        price=(499, 1999),
        materials=["leather", "rubber"],
        gender=["men", "women", "unisex"],
        sizes=["6", "7", "8", "9"],
        n=6,
        desc="Everyday sandals with an adjustable strap and a {material} footbed.",
    ),
    dict(
        name="T-Shirt",
        category="Apparel",
        brands=APPAREL,
        price=(399, 1499),
        materials=["cotton", "polyester"],
        gender=["men", "women", "unisex"],
        sizes=["S", "M", "L", "XL"],
        n=10,
        desc="A regular-fit crew neck t-shirt made from soft {material}.",
    ),
    dict(
        name="Shirt",
        category="Apparel",
        brands=APPAREL,
        price=(799, 2999),
        materials=["cotton", "linen"],
        gender=["men", "women"],
        sizes=["S", "M", "L", "XL"],
        n=8,
        desc="A button-down shirt in breathable {material} for work or weekends.",
    ),
    dict(
        name="Kurta",
        category="Apparel",
        brands=APPAREL,
        price=(899, 3499),
        materials=["cotton", "linen", "silk"],
        gender=["men", "women"],
        sizes=["S", "M", "L", "XL"],
        n=8,
        desc="A straight-cut kurta in {material} with a mandarin collar.",
    ),
    dict(
        name="Jeans",
        category="Apparel",
        brands=APPAREL,
        price=(1199, 3999),
        materials=["denim"],
        gender=["men", "women"],
        sizes=["28", "30", "32", "34"],
        n=6,
        desc="Slim-fit stretch {material} jeans with a mid rise.",
    ),
    dict(
        name="Hoodie",
        category="Apparel",
        brands=APPAREL,
        price=(999, 2999),
        materials=["cotton", "fleece"],
        gender=["men", "women", "unisex"],
        sizes=["S", "M", "L", "XL"],
        n=6,
        desc="A warm pullover hoodie in {material} with a front pocket.",
    ),
    dict(
        name="Wireless Earbuds",
        category="Electronics",
        brands=TECH,
        price=(999, 6999),
        materials=["plastic"],
        gender=None,
        sizes=None,
        n=7,
        warranty="1 year",
        desc="True wireless earbuds with Bluetooth 5.3 and a charging case for up to 24 hours of playback.",
    ),
    dict(
        name="Power Bank",
        category="Electronics",
        brands=TECH,
        price=(799, 2999),
        materials=["aluminium", "plastic"],
        gender=None,
        sizes=None,
        n=5,
        warranty="1 year",
        desc="A compact 10000 mAh power bank with fast charging and two USB ports.",
    ),
    dict(
        name="Smartwatch",
        category="Electronics",
        brands=TECH,
        price=(1999, 8999),
        materials=["silicone", "metal"],
        gender=None,
        sizes=None,
        n=5,
        warranty="1 year",
        desc="A smartwatch with heart-rate tracking, sleep monitoring and a water-resistant body.",
    ),
    dict(
        name="Bluetooth Speaker",
        category="Electronics",
        brands=TECH,
        price=(1299, 4999),
        materials=["plastic", "fabric"],
        gender=None,
        sizes=None,
        n=5,
        warranty="1 year",
        desc="A portable Bluetooth speaker with deep bass and a waterproof casing.",
    ),
    dict(
        name="Bedsheet",
        category="Home",
        brands=HOME,
        price=(699, 2499),
        materials=["cotton", "microfibre"],
        gender=None,
        sizes=["single", "double", "king"],
        n=6,
        desc="A fitted bedsheet in {material} with two matching pillow covers.",
    ),
    dict(
        name="Electric Kettle",
        category="Home",
        brands=HOME,
        price=(799, 2499),
        materials=["steel", "plastic"],
        gender=None,
        sizes=None,
        n=4,
        warranty="2 years",
        desc="A 1.5 litre electric kettle with auto shut-off and a {material} body.",
    ),
    dict(
        name="Water Bottle",
        category="Home",
        brands=HOME,
        price=(299, 1299),
        materials=["steel", "plastic"],
        gender=None,
        sizes=None,
        n=5,
        desc="A leak-proof water bottle with a {material} body that keeps drinks cold for hours.",
    ),
    dict(
        name="Backpack",
        category="Bags",
        brands=SPORT + APPAREL,
        price=(699, 2999),
        materials=["nylon", "polyester"],
        gender=["unisex"],
        sizes=None,
        n=6,
        desc="A 25 litre backpack with a padded laptop sleeve and a water-resistant {material} shell.",
    ),
    dict(
        name="Wallet",
        category="Bags",
        brands=APPAREL,
        price=(299, 1499),
        materials=["leather"],
        gender=["men", "women"],
        sizes=None,
        n=4,
        desc="A slim bifold wallet in genuine {material} with six card slots.",
    ),
]


def make_catalogue() -> list[dict]:
    """The small synthetic catalogue, with exactly three blue running shoes so that 'the blue running
    shoe' is an ambiguous question."""
    products, used, counter = [], set(), 1
    for t in TYPES:
        made = 0
        while made < t["n"]:
            brand, model = rng.choice(t["brands"]), rng.choice(MODELS)
            if (brand, model, t["name"]) in used:
                continue
            used.add((brand, model, t["name"]))
            material = rng.choice(t["materials"])
            color = rng.choice(COLORS) if t["category"] != "Home" or t["name"] == "Bedsheet" else rng.choice(COLORS)
            attrs = {"brand": brand, "color": color, "material": material}
            if t["gender"]:
                attrs["gender"] = rng.choice(t["gender"])
            if t["sizes"]:
                attrs["size"] = t["sizes"]
            if t.get("warranty"):
                attrs["warranty"] = t["warranty"]
            lo, hi = t["price"]
            products.append(
                {
                    "id": f"P{counter:04d}",
                    "title": f"{brand} {model} {t['name']}",
                    "description": t["desc"].format(material=material),
                    "category": t["category"],
                    "attributes": attrs,
                    "price": int(round(rng.uniform(lo, hi), -1)) - 1,
                    "in_stock": rng.random() > 0.15,
                    "rating": round(rng.uniform(3.2, 4.8), 1),
                    "rating_count": rng.randint(12, 2400),
                }
            )
            counter += 1
            made += 1
    # trap: exactly three blue running shoes, so "the blue running shoe" is ambiguous
    shoes = [p for p in products if p["title"].endswith("Running Shoes")]
    for i, p in enumerate(shoes):
        p["attributes"]["color"] = "blue" if i < 3 else rng.choice([c for c in COLORS if c != "blue"])
    # make sure a single black t-shirt is not accidentally the only one, and one cotton kurta is cheap
    return products


POLICY = (
    "Returns\n"
    "Items can be returned within 30 days of delivery if they are unused and in the original packaging. "
    "Refunds are issued to the original payment method within 7 business days of us receiving the "
    "returned item. Earbuds and other in-ear products cannot be returned once opened.\n"
    "\n"
    "Exchanges\n"
    "Apparel and footwear can be exchanged for a different size within 15 days of delivery, subject to "
    "availability. Exchange pickups are free of charge.\n"
    "\n"
    "Shipping\n"
    "Standard shipping takes 3 to 5 business days across India. Orders above ₹999 ship free; for smaller "
    "orders a flat shipping fee of ₹49 applies. We do not ship outside India. Cash on delivery is "
    "available for orders up to ₹5,000.\n"
    "\n"
    "Warranty\n"
    "Electronics carry a manufacturer warranty from the date of delivery; the warranty period is listed "
    "on each product. Footwear and apparel have no warranty but are covered by the returns policy. The "
    "warranty does not cover physical damage or water damage.\n"
    "\n"
    "Cancellations\n"
    "Orders can be cancelled free of charge before they are dispatched. Once an order has been dispatched"
    " it cannot be cancelled, but it can be returned after delivery.\n"
)


def is_match(p, *, title_has=None, filters=None, max_price=None, min_price=None):
    """Whether a product fits a title word, attribute values and a price range; used to work out expectations."""
    if title_has and title_has.lower() not in (p["title"] + " " + p["category"]).lower():
        return False
    for k, v in (filters or {}).items():
        val = p["attributes"].get(k)
        if (v not in val) if isinstance(val, list) else (val != v):
            return False
    if max_price is not None and p["price"] > max_price:
        return False
    return not (min_price is not None and p["price"] < min_price)


def make_questions(products: list[dict]) -> list[dict]:
    """The standard question set for a catalogue (facts, searches, ambiguity, refusals, policy,
    multi-turn), with expectations computed by brute force over the catalogue."""
    qs: list[dict] = []

    def add(turns, behaviour, **expected):
        """Add one question with its expected behaviour and details."""
        qs.append(
            {
                "id": f"q{len(qs) + 1:03d}",
                "turns": turns if isinstance(turns, list) else [turns],
                "expected": {"behaviour": behaviour, **expected},
            }
        )

    def unique(**kw):
        """Products matching the given constraints."""
        return [p for p in products if is_match(p, **kw)]

    # --- specific facts about a uniquely named product ---
    for p in rng.sample(products, 8):
        add(f"price of {p['title']}?", "answer", product_ids=[p["id"]], must_include=[f"{p['price']:,}"])
    for p in rng.sample(products, 4):
        add(f"{p['title']} kitne ka hai", "answer", product_ids=[p["id"]], must_include=[f"{p['price']:,}"])
    for p in rng.sample(products, 4):
        add(
            f"is {p['title']} in stock",
            "answer",
            product_ids=[p["id"]],
            must_include=["in stock" if p["in_stock"] else "out of stock"],
        )
    for p in rng.sample(products, 3):
        add(f"rating of {p['title']}", "answer", product_ids=[p["id"]], must_include=[str(p["rating"])])
    p = rng.choice(products)
    add(
        f"what is the material of {p['id']}",
        "answer",
        product_ids=[p["id"]],
        must_include=[p["attributes"]["material"]],
    )

    # --- searches with constraints (valid_ids = every product satisfying them) ---
    def search(q, **kw):
        """Add a search question: answerable if any product fits, otherwise expected to be refused."""
        ids = [p["id"] for p in unique(**kw)]
        add(q, "answer" if ids else "refuse", valid_ids=ids)

    search("cotton shirts under ₹2000", title_has="shirt", filters={"material": "cotton"}, max_price=2000)
    search("show me earbuds", title_has="earbuds")
    search("sasta running shoe dikhao", title_has="running shoes")
    search("cotton kurta 1500 ke andar", title_has="kurta", filters={"material": "cotton"}, max_price=1500)
    search("black hoodie under 2500", title_has="hoodie", filters={"color": "black"}, max_price=2500)
    search("power bank between 1000 and 2500", title_has="power bank", min_price=1000, max_price=2500)
    search("leather wallet for men", title_has="wallet", filters={"material": "leather", "gender": "men"})
    search("backpack above ₹1500", title_has="backpack", min_price=1500)
    search("waterproof speaker", title_has="speaker")
    search("smartwatch", title_has="smartwatch")
    add("best rated bedsheet", "answer", valid_ids=[p["id"] for p in unique(title_has="bedsheet")])

    # --- cheapest / superlative questions with one right answer ---
    cheapest = min(unique(title_has="power bank"), key=lambda p: p["price"])
    add("cheapest power bank price", "answer", product_ids=[cheapest["id"]], must_include=[f"{cheapest['price']:,}"])

    # --- ambiguous: several products fit, so ask which ---
    add("what's the price of the blue running shoe?", "clarify")
    add("kitna hai blue running shoe ka price", "clarify")
    multi = next(c for c in COLORS if len(unique(title_has="t-shirt", filters={"color": c})) > 1)
    add(f"rating of the {multi} t-shirt", "clarify")
    add("how much is the kettle", "clarify" if len(unique(title_has="kettle")) > 1 else "answer")
    add("price?", "clarify")
    add("I want something good", "clarify")

    # --- not in the catalogue / constraints nothing satisfies ---
    for q in [
        "do you sell iPhones?",
        "price of macbook air",
        "do you have a PS5",
        "Rolex watch price",
        "ferrari toy car",
        "sell me a laptop",
        "cotton shirts under ₹100",
        "red running shoes under ₹300",
        "is there a 55 inch TV",
        "yoga mat",
    ]:
        add(q, "refuse")
    add("blue wallet in size 99", "refuse")

    # --- policy ---
    add(
        "can I return earbuds after opening", "answer", policy_sections=["returns"], must_include=["cannot be returned"]
    )
    add("how many days to return", "answer", policy_sections=["returns"], must_include=["30 days"])
    add("return kitne din me", "answer", policy_sections=["returns"], must_include=["30 days"])
    add("do you ship outside India", "answer", policy_sections=["shipping"], must_include=["do not ship outside India"])
    add("is cod available", "answer", policy_sections=["shipping"], must_include=["Cash on delivery"])
    add("warranty on electronics", "answer", policy_sections=["warranty"], must_include=["warranty"])
    add(
        "can I cancel after dispatch", "answer", policy_sections=["cancellations"], must_include=["cannot be cancelled"]
    )
    add("do you offer gift wrapping?", "refuse")
    add("do you have a loyalty program", "refuse")

    # --- multi-turn ---
    add(
        ["show me shirts", "only cotton ones", "under ₹2,000, and I already said I want cotton"],
        "answer",
        valid_ids=[p["id"] for p in unique(title_has="shirt", filters={"material": "cotton"}, max_price=2000)],
    )
    add(
        ["running shoes", "blue ones"],
        "answer",
        valid_ids=[p["id"] for p in unique(title_has="running shoes", filters={"color": "blue"})],
    )
    add(
        ["I want a kurta", "cotton", "under 1000"],
        "answer" if unique(title_has="kurta", filters={"material": "cotton"}, max_price=1000) else "refuse",
        valid_ids=[p["id"] for p in unique(title_has="kurta", filters={"material": "cotton"}, max_price=1000)],
    )
    p = rng.choice(products)
    add(
        [f"tell me about {p['id']}", "what's the price?"],
        "answer",
        product_ids=[p["id"]],
        must_include=[f"{p['price']:,}"],
    )
    add(["running shoes", "blue ones", "the cheapest"], "answer")
    return qs


def main() -> None:
    """Write the catalogue, the policy and the questions into data/."""
    products = make_catalogue()
    (OUT / "catalogue.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in products) + "\n", encoding="utf-8"
    )
    (OUT / "policy.txt").write_text(POLICY, encoding="utf-8")
    qs = make_questions(products)
    (OUT / "test_questions.jsonl").write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in qs) + "\n", encoding="utf-8"
    )
    write_out(f"wrote {len(products)} products, {len(qs)} questions to {OUT}")


if __name__ == "__main__":
    main()

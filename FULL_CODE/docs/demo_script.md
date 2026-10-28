# GroundTruth demo script (about 90 seconds)

Every question below was run against the problem pack's catalogue and policy before writing this; each reply
passed the grounding check. They are our own wording, not the pack's dev or test questions.

## Before you start

```bash
cd FULL_CODE
python3 main.py serve --snapshot 2026-10-01      # http://127.0.0.1:8000, retailer view at /insights
```

Have a second terminal ready for the numbers at the end. Open the page on a phone too if the room allows.

## The walkthrough

| # | Type this | What it shows | Point at |
|---|---|---|---|
| 1 | `blue running shoes ka price` | **The trap.** Several blue running shoes, different prices: it asks which one instead of guessing | The candidates differ by gender and material; no price is quoted |
| 2 | `cotton kurti for women`, then `1500 ke andar`, then `sabse sasti` | **Multi-turn budget (the stretch goal).** Material, gender and budget carry over; "sabse sasti" names one product | Threadcraft Roshni Cotton Kurti, ₹409, in stock; the "Remembered" panel |
| 3 | `iphone 15 price` | **Refusal.** The store doesn't sell iPhones, and it says so | "I don't have iPhone products in the catalogue." |
| 4 | `return kitne din me kar sakte hai` | **Policy, cited by sub-section** | Tap the 1.1 chip: the policy section opens with the sentence highlighted |
| 5 | `sunday delivery hogi?` | **The policy is silent:** it says so instead of inventing a yes, then quotes what the policy does say | Cites 2.2, delivery timelines |
| 6 | `chargit ripplezip black speaker paani me chalega?` | **Missing ≠ no.** The catalogue lists no water resistance for this speaker, so it refuses rather than guess | "The catalogue does not list ..." |
| 7 | `sabse sasta 65W charger` | **Measurements and superlatives:** 65 W read as wattage, one cheapest product | Zentrix MiraPlus 200 65 W charger, ₹599 |
| 8 | Tap **Compare** on two product cards | **Side-by-side from catalogue records**, differences shaded | The table cells come only from cited records |
| 9 | Open `/insights` | **Retailer view:** what shoppers asked for that isn't stocked, replies failing the grounding check (0) | The "Not in catalogue" list is a demand signal |

On every reply, point at the **check badge** ("3 sources checked"): an independent checker re-derives each price and
fact from the cited record. And at the time: about 1 ms per reply, with no model call.

## The numbers (second terminal)

```bash
uv run python main.py score --baseline --part check   # official scorer, the 20 dev questions never tuned on
python3 main.py report                                # latency and token cost over all 190 pack questions
```

Say it plainly:

* Official scorer, dev questions we never tuned on: **0.865**, against the shipped baseline's 0.8275 on the same 20
  questions (small sample). All 40 dev: 0.9325 against 0.7315.
* **0 hallucinated prices** in every run. Prices are copied from the catalogue by code; the reply cannot contain a
  rupee amount that isn't a cited product's price or MRP, a policy amount, or the shopper's own budget.
* **0 model tokens, about 1 ms per query.** We measured the provided 0.5B model: 13 s and about 3,800 tokens per
  query, and it didn't help, so it's off.

## Likely questions

**Why no LLM?** We measured the provided models instead of assuming. The embedding fallback lowered the dev score
(0.92 against 1.00 on the tune half); the 0.5B instruct model took 13.4 s a call and cut its own JSON off. Rules
answer in about 1 ms with nothing invented. The fallback code is still there, with timeouts and fall-back, for a
larger model.

**How can you be sure it never invents a price?** Replies are built from catalogue fields in code, every claim is
recorded with its source, and the same checker the evaluation uses re-checks every reply live (the badge).

**What doesn't it handle?** Unusual synonyms it has never seen are refused rather than guessed; "which is better"
between two kinds of product gets a clarifying question, because the catalogue has nothing to judge "better" by.
Our scores come from small samples and from sets we wrote ourselves, so the hidden test score may be lower.

**What happens with a new catalogue?** The vocabulary (colours, brands, materials, product types) is read from the
catalogue itself; hand-written words (synonyms, Hinglish) are in `groundtruth/resources/vocabulary.json`.

# Changes

## Why

The assistant scored 1.0 on its own question sets, but those were written together with the code. On 60
shopper-style questions written independently (`data/generate_independent.py`) it scored **0.827**: answerable
questions were refused because a word was outside the catalogue's vocabulary ("jogging", "chappal", "medium size"),
single-keyword policy questions were refused ("refund kab milega"), and brands the shop doesn't carry were answered
as if it did ("nike running shoes").

## Results

`final = 0.35*grounded + 0.35*helpful + 0.30*refusal - 0.10*hallucinated_prices` (the repo's assumed weights, not
the official rubric). Measured on the synthetic data; the real catalogue and hidden questions will differ.

| Question set | Before | After | Note |
|---|---|---|---|
| `data/test_questions.jsonl` (63) | 1.000 | 1.000 | written with the code: regression check |
| `data/hard_questions.jsonl` (57) | 0.995 | 1.000 | written with the code: regression check |
| `data/scale/test_questions.jsonl` (63) | 1.000 | 1.000 | written with the code: regression check |
| `data/scale/independent_questions.jsonl` (60) | 0.827 | 1.000 | fixes were tuned on this set, so "after" is overfit |
| **`data/scale/heldout_questions.jsonl` (37)** | **0.769** | **0.942** | **never tuned on: the only generalisation estimate** |

Held-out detail (before -> after): helpfulness 0.526 -> 0.857, refusal accuracy 0.784 -> 0.973, groundedness
1.0 -> 1.0, hallucinated prices 0 -> 0. One held-out failure ("adidas hoodie") exposed a bug in the new brand list
(names ending in "s" were stemmed before the check). That bug was fixed after the held-out run (0.934 -> 0.942), so
the held-out set is very slightly contaminated.

All held-out queries were answered by the rule-based path (`path_share`: rules 1.0). The embedding and LLM
fallbacks were **not** run with real models here (they weren't available), so their effect is unmeasured.

Latency: about 12% slower per query on the same machine (`bench.py`, 1 thread: ~0.70 ms -> ~0.80 ms mean).

## Known remaining failures (held-out, deliberately not fixed)

* "school bag": wallets come back, because the "Bags" category contains wallets and "school" is unknown.
* "sabse acchi rating wali smartwatch": "sabse acchi" (best) isn't recognised, so it asks which smartwatch instead of
  ranking by rating.
* "earbuds" then "not black": an exclusion-only follow-up loses the product type and searches the whole catalogue.

Also not handled: "warranty on tv" with no brand named (only brands in `text.py`'s `EXTERNAL_BRANDS` are caught),
and the brand list itself is hand-written world knowledge.

## What changed, by commit

1. **Vocabulary and routing fixes** (`text.py`, `understand.py`, `index.py`, `policy.py`, `assistant.py`)
   * Policy: one strong keyword (refund, cancel, exchange, ...) is enough; "cancelled" now matches "cancel";
     delivery and shipping are one word, so delivery-time questions reach the Shipping section; place names
     outside India reach the "We do not ship outside India" sentence.
   * Sizes: "medium", "M size", "size medium", "king size", "double" map to catalogue sizes; an explicit size the
     catalogue doesn't stock ("size 50") still refuses; a bare size word is ignored for products without sizes.
   * Product words: a recognised product type plus unknown modifiers ("sneakers for gym") now answers with an
     "I can't verify 'gym'" note instead of refusing; a colour alone no longer licenses an unknown product ("red
     saree" refuses). Hinglish and gender words added (chappal, kurti, ladies, gents, ...); "men"/"women" include
     unisex items.
   * Brands not carried (nike, samsung, adidas, ...) are refused with cited alternatives, including inside policy
     questions ("warranty on samsung tv"). Unsold products with a close relative ("headphones") list the relative.
   * "does <kettle> have bluetooth" checks the product instead of reciting its price.
   * "is the grey backpack in stock" asks which one instead of listing.
   * Function words are no longer stemmed ("does" -> "doe").
   * Tests: `tests/test_vocabulary_fixes.py`. Also adds the independent and held-out question generators
     (`data/generate_independent.py`, `data/generate_heldout.py`).
2. **Catalogue loading** (`catalogue.py`): prices like "₹1,299" / "Rs. 1299.00", stock like "Only 2 left" (was
   parsed as out of stock), ratings like "4.2/5", flat attribute columns, CSV BOM and header case, wrapped JSON.
   Bad rows are skipped and reported instead of crashing the load. A missing stock field is now "not listed" instead
   of being asserted as "in stock". Tests: `tests/test_catalogue_loading.py`.
3. **Model fallbacks, miss path only** (`semantic.py`, `llm_normaliser.py`, `calibrate_semantic.py`, `chat.py`,
   `evaluate.py`, `bench.py`): see README "Fallbacks". `evaluate.py` adds `path_share` and real LLM token counts.
   `bench.py`'s default question path pointed at a file the generator never creates; fixed. Tests use fake models:
   `tests/test_fallbacks.py`.
4. **Docs**: README commands fixed and extended, and this file.

## Web UI (later commit)

`web.py` and `web/index.html`: a local, offline web interface (see README "Web UI"). The problem statement scores a
notebook, CLI and web page equally, so this does not change `evaluate.py`'s score; it exists to make grounding visible
to a human reviewer. Also in this commit: brand names in refusals keep their own spelling ("iPhone", not "Iphone").
Tests: `tests/test_web.py`. Scores on all evaluation sets are unchanged.

## Compare, retailer view, snapshot date, and two engine fixes (later commit)

* **Engine fix: comparing by product id.** "compare P0001 and P0002" used to recognise only the first id and refuse.
  All ids in a message are now read; two or more make a comparison. Comparison words ("compare", "vs") are filler.
* **Engine fix: unsold item as a follow-up.** After "black hoodie", asking "laptop" used to re-show the black hoodies
  as an answer (present in the original code too). Now, when a follow-up's new words match none of the products
  being discussed, the assistant refuses and keeps the earlier search intact. Found by testing the UI, not from the
  held-out set, so the held-out score (0.942, unchanged) is not affected by it.
* **Compare in the UI**, built on the engine's existing comparison: the table only shows fields from cited records.
* **Retailer view** (`/insights`) and CSV export of refused requests; counts and short labels only.
* **Snapshot date** on every price (`--snapshot`, default: catalogue file date, labelled as such).
* **Budget refusals say what was missing and what exists**: "No cotton kurta under ₹1,500 in the catalogue; the
  cheapest cotton kurta I have is ... at ₹2,509 [P0034]" instead of "I don't have that in the catalogue".
* **Most relevant policy sentence first** ("delivery to dubai" now opens with "We do not ship outside India").
* `web.py` defaults to the 5,000-product demo catalogue (generated on first run); example questions are built from
  whichever catalogue is loaded and checked at start-up; refusals are labelled "Not in catalogue", not as failures;
  the grounding check in the UI sees the whole chat, as `evaluate.py` does.
* **Web UI redesigned as a retail app in beige and brown tones.** Cited product lines render as product cards (colour swatch from the
  product's own colour attribute, price tag, rating, stock, key attributes, all from the cited record) that flip to
  the full record; clarifying replies get a "This one" button per card; the exact reply text the evaluation scores
  stays one tap away under every reply. The retailer view matches. Hinglish question words ("kab", "kaise") are
  treated as filler.
* Roadmap trimmed to what is not built. Not built, deliberately: voice input (browser speech recognition sends audio
  to a cloud service, which breaks the offline requirement), photo search (no vision model is provided), price-drop
  alerts and live sync (no live price feed; it would be a mock).

## Round 3: shopper phrasing (later commit)

Found by probing with new, realistic messages, then fixed (tests: `tests/test_shopper_phrasing.py`):

* The three known held-out failures: "school/college/laptop bag" means backpack; Hinglish ranking words ("sabse
  acchi", "sabse sasta", "sabse mehenga", "badhiya") sort the results; an exclusion-only follow-up ("not black")
  refines the earlier search instead of searching everything.
* "not nike" excludes a brand the shop doesn't carry instead of refusing; on its own it asks what is wanted.
* "noise cancellation" is a feature, not the brand Noise and not the cancellation policy.
* Size and attribute questions about one product ("P00725 ka size kya hai", "colour of P00725") are answered from
  the record. A bug where "size kya" read "kya" as a size, and "sizes" as size S, is fixed.
* A filter on an attribute the product type doesn't have ("smartwatch for women" when smartwatches list no gender) is
  dropped with a note instead of causing a refusal.
* Orders, tracking, coupons, discounts, payments, gift cards and store locations get one clear refusal: the assistant
  can only see the catalogue and policies. "thanks" and "help" get a short reply. "cheapest thing you have" lists the
  cheapest items overall. Hindi filler ("kya aapke paas ... hai") is ignored.
* **A describing word alone no longer makes an unsold product answerable**: "wireless mouse" used to list wireless
  earbuds. Unknown words now count as modifiers only when the message also names what a product is ("shoes",
  "kettle"). Trade-off: an unusual synonym with only a describing word may now be refused ("trainers" was added as a
  synonym for shoes).
* "queen size bedsheet" refuses when no bedsheet comes in queen; spoken size words stay soft for products without
  sizes ("large size water bottle").

### Honest numbers for this round

| Set | Before round 3 | After | Note |
|---|---|---|---|
| `heldout_questions.jsonl` (37) | 0.942 | 1.000 | its failures were fixed here, so it is now a regression set |
| **`heldout2_questions.jsonl` (45)** | **0.987** | 1.000 | **written after the other fixes, scored once (0.987), then its two failures (wireless mouse, queen size) were fixed; it is now contaminated too** |

The last clean generalisation estimate is therefore **0.987** (groundedness 1.0, helpfulness 1.0, refusal accuracy
0.956, zero hallucinated prices), on synthetic data, with questions written by one person. Write a new held-out set,
ideally by someone who hasn't seen the code, before trusting a higher number.

## Task 1: restructure to the guidelines layout (no behaviour change), 27–28 October

All code moved into `FULL_CODE/` with `main.py` as the one entry point, split into single-purpose packages, with
settings in `config.json`, logging instead of `print`, a lockfile, ruff, LF line endings and an MIT licence.

**Proof of no behaviour change.** After every stage, all six question sets were evaluated and compared line by line
with the previous commit (every report column except latency, plus the summary). Replies and scores were identical at
each stage. The comparison scripts lived in a temporary folder and are not part of the repository.

| Commit | Change | Check |
|---|---|---|
| `d098c6e`, `c930fce` | Move into `FULL_CODE/`, `main.py`, shared grounding checker, `api/` split | identical to `1321d50` (with `PYTHONHASHSEED=0`) |
| `c8bd0a8` | Compared products listed in the order the shopper named them | **behaviour fix**, see below |
| `d384b1d` | Hard-question generator independent of the hash seed | **data fix**, see below |
| `d9180bc` | `understanding/`, `answering/`, `catalogue/`, `policy/`, `models/` packages | identical to `d384b1d` |
| `8d8e35d` | `config.json` + validation, vocabulary file, logging, `builder.py` | identical to `d9180bc` |
| this commit | `pyproject.toml`, `uv.lock`, ruff lint + format, stopwords into the vocabulary file, `.gitattributes`, `LICENSE`, README | identical to `8d8e35d` (on Python 3.9 and 3.12) |

`d098c6e` was pushed without some of its files (a paste split the `git add` command), so it and `c8bd0a8` don't run
on their own; `c930fce` completes them. History was left as pushed.

**Two determinism bugs found and fixed** (both were already in `1321d50`). Python randomises string hashing per
process, and two places iterated a `set`:

* `index.mentioned()`: a compare reply's product order varied between runs (h021). It now follows the order the
  shopper named the products. A new test fails on the old code.
* `generate_hard.py`: question h029's brand varied between runs. It now picks from the sorted brands, and
  `data/hard_questions.jsonl` was regenerated (h029: Aerotrek -> Velora).

Afterwards, all six sets give identical replies under hash seeds 0–3.

| Measure | Before (`1321d50`) | After |
|---|---|---|
| Tests | 88 passing | 96 passing (determinism test, 7 config tests) |
| Scores, all six sets | 1.0 (groundedness 1.0, 0 hallucinated prices) | unchanged |
| Ruff lint issues (`E,F,W,I,B,UP,SIM`, line length 120) | 76 (measured at `8d8e35d`, after the split) | 0, no rules disabled |
| Files ruff would reformat | 51 of 55 | 0 |
| Lockfile / `pip-audit` | none / not run | `uv.lock`; no known vulnerabilities |
| `print` calls | 28 | 0 (logging for events, `write_out` for command output) |
| Files with CRLF line endings | 13 | 0 (`.gitattributes`: `eol=lf`) |
| Largest module | `assistant.py`, 455 lines | `answering/assistant.py`, 261 lines after formatting |
| Mean latency, 5,000 products (`main.py bench`) | ~0.42 ms | ~0.44 ms (same machine, run-to-run noise) |

**Python versions.** With the dev tools on Python 3.9, `pip-audit` found 11 known vulnerabilities in pytest and in
pip-audit's own dependencies (requests, urllib3, filelock, msgpack). Their fixes are released only for Python 3.10+.
So the lockfile now targets 3.10+ (`[tool.uv] environments`) and the dev environment runs on 3.12, where
`pip-audit` finds none. The app has no dependencies and is still tested on Python 3.9: the same 96 tests pass with
`python3 -m unittest`, and all six sets give identical replies.

No evaluation set was tuned on in Task 1, so contamination is unchanged: the last clean generalisation estimate is
still **0.987** (held-out set 2).

## Before submitting

1. Run `python main.py calibrate --embed-model PATH` with the provided embedding model and read its table. Add words
   from the real catalogue to its lists in `tools/calibrate_semantic.py`.
2. Run `python main.py evaluate` with `--embed-model` and `--llm-model` on a fresh held-out set and compare against the
   rules-only score. Keep a fallback only if it helps.
3. Adapt `load_cases` in `groundtruth/evaluation/scoring.py` to the real test-file format.

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
* Roadmap trimmed to what is not built. Not built, deliberately: voice input (browser speech recognition sends audio
  to a cloud service, which breaks the offline requirement), photo search (no vision model is provided), price-drop
  alerts and live sync (no live price feed; it would be a mock).

## Before submitting

1. Run `calibrate_semantic.py` with the provided embedding model and read its table. Add words from the real
   catalogue to its lists.
2. Run `evaluate.py` with `--embed-model` and `--llm-model` on a fresh held-out set and compare against the
   rules-only score. Keep a fallback only if it helps.
3. Adapt `evaluate.py`'s `load_cases` to the real test-file format.

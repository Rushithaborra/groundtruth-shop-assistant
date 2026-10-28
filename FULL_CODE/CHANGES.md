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

## Task 2: the organisers' data and format, 28 October

The problem pack (5,000-product catalogue, Markdown policy, 40 dev questions with expected behaviour, 150 test
questions, `evaluate.py`, a baseline) lives in the git-ignored `data/pack/`. Its README changed several assumptions:
submissions are **structured** (cited products and policy sections, stated prices and facts under exact catalogue
field names); the score is `action_correct × (0.4 + 0.3·grounded + 0.3·helpful) − 3 × hallucinated prices`; clarify
only when the matching products differ in what was asked; refuse when an attribute is absent (except a missing
`warranty_months`, which policy 3.1 answers).

| Commit | Change |
|---|---|
| `7cb6fd5` | Load the pack: keep the catalogue's own field names, `mrp_inr` and the 3-way `stock_status`; Markdown policy split into its 30 numbered sub-sections; question CSVs with multi-turn conversations |
| `1033c1e` | Every reply records its citations, prices and facts as structured claims, copied from the catalogue record in code |
| `df35f0d` | `main.py submit` writes `submission/R3_predictions.jsonl` (validated: one line per question, required keys, prices only for cited products) |
| this commit | `main.py score` runs the pack's `evaluate.py` unchanged, beside the pack's baseline |

**Honest starting point** (official scorer, 40 dev questions, `uv run python main.py score --baseline`). This is the
assistant as built for the synthetic data, before any change for the pack's rules:

| | Shipped baseline | GroundTruth (untuned) |
|---|---|---|
| **Primary score** | **0.7315** | **0.6738** |
| Action accuracy | 0.825 | 0.713 |
| Grounded / helpful (action right) | 0.849 / 0.774 | 0.931 / 0.891 |
| Refusal P / R | 0.857 / 0.600 | 0.500 / 0.700 |
| Clarify P / R | 0.667 / 1.000 | 0.750 / 0.750 |
| Hallucinated prices | 0 | 0 |
| Mean latency | 2.27 ms | 1.26 ms |

| Question type (n) | Baseline | GroundTruth |
|---|---|---|
| answer_lookup (9) | 0.767 | 0.889 |
| answer_attribute (3) | 0.850 | 0.283 |
| answer_filter (3) | 0.800 | 0.300 |
| answer_superlative (2) | 0.700 | 0.200 |
| answer_compare (1) | 0.900 | 0.900 |
| answer_policy (4) | 0.450 | 0.600 |
| clarify_ambiguous (8) | 0.914 | 0.812 |
| refuse_out_of_catalogue (6) | 0.500 | 0.833 |
| refuse_unanswerable (3) | 0.667 | 0.333 |
| refuse_policy_gap (1) | 1.000 | 1.000 |

What to fix (on dev only; the 150 test questions are only ever used to write the submission): attribute and
superlative questions, "show me" filters, refusing facts the catalogue doesn't list, and clarifying only when the
matches differ. The committed `submission/R3_predictions.jsonl` is from this untuned version and will be regenerated.

Development dependencies added: numpy 2.2.6 and pandas 2.3.3 (the official scorer and baseline need them; the
assistant doesn't). `pip-audit` still finds no known vulnerabilities. 117 tests pass on Python 3.9 and 3.12.

### Aligning with the pack's rules (tuned on half of dev only)

To keep an untuned estimate, the 40 dev questions were split once into a **tune** half and a **check** half
(`tools/split_dev.py`, seed 2026, conversations kept whole, question types dealt alternately; the IDs are in
`data/dev_split.json`). Per-question results were read only for the tune half; the check half was scored as a whole
and never inspected. The 150 test questions are used only to write the submission.

Tune-half failures, each fixed as a general rule rather than for the exact wording:

| Failure (tune half) | General fix |
|---|---|
| "65W charger dikhao" refused ("65w" unknown) | Measurements (`65W`, `6 mm`, `5.5 litre`, `500 piece`) are constraints on attributes in that unit (`output_w`, `thickness_mm`, ...); ignored when no candidate has such an attribute |
| "king size ... bedsheet sabse sasti jo stock me ho" listed a double bedsheet | A size word that isn't a stocked size but is another attribute's value ("king" → `bed_size`) is read as that attribute; a superlative ("sabse", "cheapest") names one product with its price; with a stock word, only orderable products |
| "... speaker paani me chalega?" listed the speaker | Hinglish attribute words (`moti` → thickness, `paani` → water) and every key sharing a word (`water_resistant`, `water_resistance_rating`); an attribute the product doesn't list is refused ("the catalogue doesn't say") |
| "... mat kitni moti hai" answered the price | "kitna/kitni/kitne" with an attribute word asks about the attribute; the price is stated only when explicitly asked |
| "... duffel me shoe compartment hai?" refused | Multi-word attribute names ("shoe compartment") are matched before product words; the synonym `jute → shoe` removed (jute is a catalogue material) |
| "Chandni kurti kitne ki hai" refused | Attribute filters match every catalogue value that reads the same ("Kurtis" and "Kurtas") |
| "GST alag se lagega kya" refused | The policy's named terms (capitalised: GST, COD, MRP, UPI) identify a policy question on their own |
| "saffron coins ya loyalty points" answered | (the first version of the previous fix used any word found in only one policy section, which also matched the store's name; narrowed to named terms) |

Two synthetic regressions appeared and were fixed before the final check: "is there a restocking fee" answered with
the shipping fee (the single-section rule; replaced by named terms), and "do shoes have warranty" clarified (English
"do / have / is" counted as attribute-question words; only Hinglish markers and "?" count now).

| Official scorer | Shipped baseline | GroundTruth before | GroundTruth after |
|---|---|---|---|
| Tune half (20, tuned on) | 0.6355 | 0.7125 | 1.0000 |
| **Check half (20, never inspected)** | **0.8275** | **0.6350** | **0.8400** |
| All 40 dev (includes the tuned half) | 0.7315 | 0.6738 | 0.9200 |
| Hallucinated prices | 0 | 0 | 0 |

**The clean estimate is the check half: 0.840**, a small lead over the baseline's 0.8275 on 20 questions, which is
within noise. The check half was measured once before the two synthetic-regression fixes and again after; both
gave 0.840. All 40 dev, by type (after): lookup 1.00, filter 1.00, superlative 1.00, refuse_unanswerable 1.00,
policy gap 1.00, clarify 0.94, compare 0.90, policy 0.85, out-of-catalogue 0.83, attribute 0.67.

The six synthetic sets still score 1.0 (reply wording changed where the new rules apply). The superlative test now
expects one product with its price instead of a list, as the pack's superlative questions require. 127 tests pass
(10 new, on a pack-style catalogue built in the test). `submission/R3_predictions.jsonl` is regenerated
(answer 66, clarify 33, refuse 51).

Dev is now fully used: the tune half by tuning, the check half by one measurement. The next generalisation number
has to come from new questions (Task 4).

## Task 3: the provided models, measured, 28 October

Downloaded with the pack's own script (`--set small`): MiniLM-L6-v2 embedding (safetensors, 97 MB) and
Qwen2.5-0.5B-Instruct (GGUF q4_k_m, 480 MB), Apache-2.0, into the git-ignored `models/`.

**Code** (commit `0d14a92` and this one): the GGUF model loads through llama.cpp (greedy, JSON mode, real token
counts); the embedding loads from local files with no remote code. Every model call goes through `call_model`:
timeout, retry with exponential back-off for temporary errors only (a prompt longer than the context fails at once),
fail-fast while an earlier timed-out call is still running (otherwise every later call queued behind it and timed
out too, which the first measurement showed), and a fall-back to the rule-based answer. Settings are in
`config.json`; `llm_context_tokens` is 8192 because the grounded prompt needs about 3,800 tokens.

**Calibration** on the pack catalogue (`main.py calibrate`): only one of the tool's example synonyms exists in this
catalogue (sweatshirt → hoodie 0.705), while unsold words came close (jacket → hoodie 0.616, mobile → "gsm" 0.606),
and the product-type list includes non-types ("gsm", "mah", "sport"). For policy retrieval, 4 of 6 example
questions pointed to the right sub-section, but only 2 cleared the calibrated threshold (0.486).

**Measured on the tune half** (official scorer, 20 questions; the check half was not used for this decision):

| Set-up | Score | Mean / p95 latency | Model calls, tokens |
|---|---|---|---|
| **Rules only (shipped)** | **1.000** | 0.9 / 2.4 ms | 0 |
| + embedding fallback | 0.920 | 7.2 / 24 ms | 0 LLM tokens |
| + embedding + LLM | 0.920 | (LLM calls timed out) | |

A direct timing of one LLM call on this laptop: 13.4 s with the grounded prompt (3,820 tokens in, 120 out), and
the JSON was cut off at the token limit while copying the material list; without the catalogue schema it took
1.4 s but returned nothing usable (`product_type: null`).

**Decision:** neither fallback helps, so both stay off; the shipped assistant makes no model calls. The model
libraries (torch, transformers, sentence-transformers, llama-cpp-python) were removed from the lockfile:
`pip-audit` reported advisories for them, one (diskcache, needed by llama-cpp-python) with no fixed version. The
README explains how to install them by hand to try the models. `pip-audit` on the lockfile: no known
vulnerabilities. 134 tests pass on Python 3.9 and 3.12 (7 for model calls). Dev scores unchanged (tune 1.000,
check 0.840).

The measured LLM cost (about 3,800 prompt tokens and 13 s a query) also gives the "a model answers every query"
comparison for the cost report in Task 6.

## Task 6: per-query latency and token-cost report, 28 October

`main.py report` answers the pack's 190 questions (40 dev and 150 test, conversations in turn order; test questions
are only answered and timed) and writes `output/reports/cost_report.csv` (one row per query: path, latency, measured
model tokens, estimated context and reply tokens, LLM-baseline estimate) and `cost_report.json` (totals,
percentiles, path share).

| | GroundTruth (measured) | LLM writes every reply (estimate) |
|---|---|---|
| Path | rules 100%, embedding 0%, LLM 0% | the provided 0.5B model |
| Latency | mean 1.29 ms, p50 0.82, p95 3.16, max 11.5 | 13.4 s a query |
| Model tokens | 0 | 727,692 in, 22,800 out over 190 queries |
| Total time, 190 queries | 0.25 s | about 2,546 s (42 min) |

The estimate multiplies one measured call (Task 3: 3,820 prompt tokens, 120 output, 13.4 s on this laptop's CPU)
by the number of queries; its inputs are in `config.json` (cost_report section). The pack gives no token budget.
Percentiles use the nearest-rank definition; a test caught an off-by-one in the first version (rounding an index).
139 tests pass on Python 3.9 and 3.12 (5 new).

## Task 9 (part): the submission zip builder, 28 October

`main.py package` clones the committed project into `TEAM-000802_R3/`, runs the guidelines' page-4 checklist
(top-level items; no `.env`, `.venv`, pack data, models, output, caches, zips or model weights; `uv.lock`,
`.env.example`, `pyproject.toml` and `main.py` present; the README's seven rule-12 sections; the predictions file
validated against the 150 test questions) and writes `output/TEAM-000802_R3.zip` only if every check passes.

Built from commit `8795189`: checklist passed; 5.5 MB; one root folder; top level `.git`, `.gitattributes`,
`.gitignore`, `FULL_CODE`, `LICENSE`, `README.md`, `submission`; 150 prediction lines. The team ID format
(`TEAM-000802` or `000802`) is still to be confirmed with the organisers; it is one setting in `config.json`.
`.gitattributes` is not in the guidelines' list of top-level files; it only fixes line endings and is allowed by
the checker. 145 tests pass (6 new for the checklist and zip; the team-ID setting is checked in the config tests).

## Task 7: web API validation, security headers and rate limits, 28 October

The routes (`api/server.py`) stay separate from the core; validation, rate limiting and security headers are
framework-free modules (`api/validation.py`, `api/ratelimit.py`, `api/security.py`) tested on their own.

* **Validation.** POST bodies must be declared JSON (415 otherwise), have a Content-Length (411) within
  `max_body_bytes` (413; never more than that is read), and be a JSON object. `message` must be text of 1 to
  `max_message_chars` characters (413 above; it used to be cut silently, which could change its meaning) with no
  control characters; `session_id` must be absent, null or a server-minted id; unknown fields are refused. Query
  strings are ignored. PUT, DELETE, PATCH, OPTIONS and HEAD get 405.
* **Other sites.** No CORS headers are sent, and a POST whose `Origin` isn't this server gets 403.
* **Headers** on every response: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  cross-origin isolation, a `Permissions-Policy`, `Cache-Control: no-store`; the Python version is no longer
  advertised. Pages get a Content-Security-Policy that allows only their own inline script (by SHA-256 hash) and
  same-origin requests; each page was checked to have one plain script, no inline handlers and nothing external.
* **Rate limits** (config.json): 30 messages a minute per session and 120 per client address (so new sessions can't
  be minted to get round it), sliding window, bounded memory; 429 with `Retry-After`.
* **Page.** Two values were inserted without escaping (the action name, a rating); both are now escaped or
  converted. Every other dynamic value already went through `esc()`. Error text no longer mentions `web.py`.
* **`main.py serve`** now shows the pack's catalogue when it's in `data/pack/` (synthetic demo otherwise).

Smoke test on the pack: 5,000 products, 30 policy sections, a "65W charger dikhao" reply verified with 3 citations;
a POST without a JSON type got 415 and a cross-site POST 403; headers present. Phone width: checked from the CSS
only (viewport meta; breakpoints at 960 px and 520 px; the compare table scrolls inside its box), not in a browser.
155 tests pass on Python 3.9 and 3.12 (10 new, including a running server with a 2-a-minute limit).

## Task 5: known behaviour gaps, 28 October

Checked on the pack with our own wording (not dev or test questions); all three were worse than listed:

| Gap | Before | Fix |
|---|---|---|
| "men's running shoes under 3000" then "which sizes" | "What are you looking for?" | The session remembers the products it last showed; an attribute question with no product of its own is answered from each of those records, cited, mentioning only keys they have ("size" also matches `bat_size`, `bed_size`, ...; single-product answers got the same filter) |
| "tshirt or shirt which is better" | refused ("better" read as a product word) | Two kinds of product joined by or / ya / vs, with a preference or question word, get a clarifying question showing a few of each (cited, no prices). Colours, materials and brands aren't kinds, and a price question ("purple ya teal, kaunsa sasta") is still answered |
| "do you deliver on sunday" / "same day delivery" | quoted delivery charges / the return window | A delivery day or time the policy never mentions (`policy_time_words` in `vocabulary.json`) gets "the policy doesn't say anything about ..." plus what it says on delivery times (2.2), as a refusal |
| "earbuds ya earphones" | both read as earbuds | The synonym earphone → earbud is removed: the pack's README says wireless earbuds and wired earphones are different products. It stays as a related suggestion for refusals |
| External-brand list in config | | already done in Task 1 (`vocabulary.json`) |
| Describing-word refusal rule | | needs the Task 4 held-out questions |

**A regression caught and fixed before committing:** the first choice rule treated colour words as product kinds,
which turned the dev comparison "... purple ya teal earbuds kaunsa sasta" into a clarification. The official check
half fell from 0.840 to 0.795. It was traced by diffing our own dev predictions against the previous commit (no
expected answers read) and fixed by limiting choices to product kinds and to questions without a price comparison.
After the fix, no dev prediction differs from the previous commit: tune 1.000, check 0.840, all 40 dev 0.920.

Synthetic sets all 1.0. One of the 150 test predictions changed (a clarification's cited products). 160 tests
pass on Python 3.9 and 3.12; the 3 new gap tests fail on the previous commit's code.

## Before submitting

1. Run `python main.py calibrate --embed-model PATH` with the provided embedding model and read its table. Add words
   from the real catalogue to its lists in `tools/calibrate_semantic.py`.
2. Run `python main.py evaluate` with `--embed-model` and `--llm-model` on a fresh held-out set and compare against the
   rules-only score. Keep a fallback only if it helps.
3. Adapt `load_cases` in `groundtruth/evaluation/scoring.py` to the real test-file format.

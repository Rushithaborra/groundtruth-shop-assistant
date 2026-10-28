# TEAM-000802 · R3: GroundTruth, the shopping assistant that doesn't lie

## What it does

**Team:** Crew (TEAM-000802) · **Problem statement:** R3, The Conversational Shopping Assistant That Doesn't Lie
(TalentAP Mega Hackathon 2026, Retail track).

GroundTruth is a product-discovery assistant over a fixed catalogue. Shoppers ask short, ungrammatical, code-mixed
questions ("cotton kurta 1500 ke andar", "price of the blue running shoe", "do you sell iPhones?") and get one of three
replies:

| Situation | Reply |
|---|---|
| The catalogue supports one answer | **Answer** built only from catalogue fields or policy sentences, each cited (`[P0012]`, `[policy:returns]`) |
| Several products fit "the" product | **Clarify**: lists the candidates and what tells them apart, without guessing a price |
| The catalogue can't support it | **Refuse** ("I don't have that"), naming the closest cited alternative when there is one |

There is no free-form text generation, so a price, product or policy that isn't in the source data cannot appear in a
reply. An independent grounding checker re-derives what every citation allows and flags anything unsupported.

## How to run

Everything runs offline on Python 3.9 or newer. The assistant itself uses only the standard library.

```bash
cd FULL_CODE
python3 main.py generate          # synthetic catalogues and question sets (data/, data/scale/)
python3 main.py chat --meta       # command-line chat; --meta shows action, latency and tokens per reply
python3 main.py serve             # web demo at http://127.0.0.1:8000, retailer view at /insights
python3 main.py evaluate          # scores + output/reports/summary.json and query_report.csv
python3 main.py bench             # throughput and latency
```

**Settings** are in `FULL_CODE/config.json` (paths, reply limits, thresholds, model paths, server limits, evaluation
weights). A few can be overridden with environment variables; see `FULL_CODE/.env.example`. No API keys or secrets
are needed.

**The problem pack's data** goes in `FULL_CODE/data/pack/` (git-ignored). Point the assistant at it with flags or
environment variables:

```bash
python3 main.py evaluate --catalogue data/pack/catalogue.jsonl --policy data/pack/policy.txt --questions data/pack/questions.jsonl
# or: export GROUNDTRUTH_CATALOGUE=data/pack/catalogue.jsonl GROUNDTRUTH_POLICY=data/pack/policy.txt
```

The loader accepts `.jsonl`, `.json` and `.csv` catalogues with common column spellings (`id|product_id|sku`,
`title|name`, prices like "₹1,299", stock like "Only 2 left", attributes as a dict, JSON string or flat columns). Bad
rows are skipped and logged. Policy text is split into sections on blank lines; a short first line becomes the
section heading and the id used in citations.

**The provided models** (optional) go in `FULL_CODE/models/` (git-ignored). Calibrate the embedding fallback first,
then pass the model folders:

```bash
python3 main.py calibrate --embed-model models/<embedding-model>
python3 main.py chat --embed-model models/<embedding-model> --llm-model models/<instruct-model>
```

## How to run the tests

```bash
cd FULL_CODE
uv sync              # once: creates .venv with the pinned dev tools from uv.lock (uv fetches Python 3.12 if needed)
uv run pytest        # 96 tests
uv run ruff check . && uv run ruff format --check .
uv export --format requirements-txt --no-hashes > /tmp/reqs.txt && uv run pip-audit -r /tmp/reqs.txt
```

The development tools (pytest, ruff, pip-audit) are locked for Python 3.10+ because their security fixes are not
released for 3.9; `.python-version` pins the dev environment to 3.12. The app itself needs no packages and still
runs on Python 3.9. To check that, run the same tests with the standard library only:
`python3 -m unittest discover -s tests`.

## Architecture

```
shopper text ─► understanding/ ──► [miss path only: models/ embedding fallback ─► LLM normaliser] ─┐
                (budget, size, filters,                                                          │
                 intent, Hinglish, typos)                                                         ▼
                                     catalogue/ index (hard constraints, ranking) ◄── answering/ (answer / clarify / refuse)
                                     policy/ sections (sentence retrieval)       ◄──     │
                                                                                          ▼
                                                     grounding/ checker ──► reply with citations, latency, tokens
```

All code is in `FULL_CODE/`, and `main.py` is the one entry point.

| Folder | Responsibility |
|---|---|
| `groundtruth/understanding/` | Parse a message: budgets (`money`), sizes, word-boundary repair (`tokens`), word lists (`lexicon`), `parser` |
| `groundtruth/catalogue/` | Tolerant catalogue `loader`, and the `index` (inverted postings, constraint filtering, ranking, typo matching) |
| `groundtruth/policy/` | Policy sections and sentence retrieval |
| `groundtruth/answering/` | `assistant` routing; reply builders for products, listings, refusals and policy; per-user `sessions` |
| `groundtruth/grounding/` | The independent checker shared by the evaluator and the web demo's live check |
| `groundtruth/models/` | Optional fallbacks using the provided models; the LLM prompt is in `models/prompts/` |
| `groundtruth/evaluation/` | Scoring and the latency / token report |
| `groundtruth/config.py` | Loads and validates `config.json` and the environment overrides |
| `api/` | Web server: routes (`server`), sessions and retailer insights (`state`), cards and examples (`views`) |
| `web/` | Static UI (no external scripts or fonts) |
| `tools/` | Data and question-set generators, benchmark, embedding calibration |
| `tests/` | Unit and behaviour tests |

The core (`groundtruth/`) does not depend on the web layer and is tested on its own.

### Web demo

![A Hinglish budget question answered with cited product cards](FULL_CODE/docs/ui-chat.png)

Product cards are built only from the cited catalogue records. Tapping a citation flips the card to the exact record or
policy section. Every reply is re-checked by the grounding checker, and the result is shown next to it. The page also
shows the remembered filters and budget, how the last message was read, a side-by-side compare, and a retailer view
(`/insights`) of what was asked for but isn't stocked.

## AI usage

**In the app.** The main path makes no model calls. Two optional fallbacks use the small models provided with the
problem pack, and only when the rule-based parser leaves product words it can't place:

1. **Embedding model** (`groundtruth/models/semantic.py`): maps an unknown shopper word to the nearest catalogue
   product-type word ("jogging" -> "running"), never to a brand, colour or material, and retrieves policy sentences for
   policy questions phrased without policy words. It stays off until `main.py calibrate` has set its thresholds.
2. **Instruct model** (`groundtruth/models/normaliser.py`): turns a messy query into JSON (product type, filters,
   budget). Every field is validated against the catalogue's own vocabulary, and a budget is accepted only if it is one
   of the shopper's own numbers. The model's text never reaches the shopper.

**Status:** both fallbacks have so far been tested only with fake models in the unit tests. Their effect with the
real provided models has not been measured yet.

**AI coding assistants.** Claude (chat) and Claude Code were used to write, refactor and review code, tests and
documentation. The team reviewed every change and can explain the code.

## Pre-built and third-party code

**Written for this hackathon before Day 1 (30 October 2026).** All the code in this repository was written for this
hackathon. These commits came before Day 1:

| Date | Commit | What |
|---|---|---|
| 2026-10-15 | `ed014d7` | Grounded assistant, evaluator and synthetic data |
| 2026-10-15 | `49effd0` | Negation, comparisons, split words, multi-intent; hard and scale test sets |
| 2026-10-15 | `b9bc130` | Inverted-index search, cached typo matching, session store, benchmark |
| 2026-10-16 | `8898bec` | Fewer false refusals, tolerant catalogue loading, model fallbacks, held-out evaluation |
| 2026-10-18 | `b28bb0f` | Compare, retailer view, snapshot dates |
| 2026-10-18 | `1321d50` | Retail UI; shopper-phrasing fixes; held-out set 2 |
| 2026-10-27 to 28 | `d098c6e` … `8d8e35d` | Restructure to the guidelines layout, config file, logging (no behaviour change) |

**Third-party code.** None is copied into the repository, and the assistant has no runtime dependencies. The
development tools are pinned in `FULL_CODE/uv.lock` (`pip-audit` finds no known vulnerabilities): ruff (MIT), pytest
(MIT) and pip-audit (Apache-2.0). The optional
model fallbacks load the provided models with Hugging Face transformers or sentence-transformers (Apache-2.0) and
PyTorch (BSD-3-Clause), installed only when the models are used.

## Limitations

* **Synthetic data so far.** The catalogue, the questions and the fixes were all written by the same people, so the
  scores below are optimistic. Every set scores 1.0 because the sets were tuned on; the last clean (untuned)
  generalisation estimate was **0.987** on held-out set 2, before its two failures were fixed. Expect lower scores on
  the organisers' catalogue and 150 hidden questions, mostly from vocabulary (synonyms, Hinglish spellings),
  ambiguity thresholds and policy wording.
* **Not yet run on the problem pack** (its catalogue, policy and question format) and **not yet run with the provided
  models**, so the real token and latency cost of the fallbacks is unmeasured.
* **The scoring weights are our assumption** (`config.json`, evaluation section) until the official rubric is known.
* **Known behaviour gaps:** "which sizes" as a follow-up after a listing; "tshirt or shirt which is better" refuses
  instead of clarifying; "do you deliver on sunday" gives delivery days without saying the policy doesn't mention
  Sundays; a describing word plus an unknown noun leans towards refusing (safer, but it may refuse unusual synonyms).
* Measured on a laptop with the 5,000-product synthetic catalogue: about 0.44 ms mean per query and about 2,300
  queries/s per process (`main.py bench`). Python threads share one interpreter lock, so scale with processes.

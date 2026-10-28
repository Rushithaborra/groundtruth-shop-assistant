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
# copy the unzipped pack folder's contents into FULL_CODE/data/pack/ (so data/pack/data/catalogue.jsonl exists)
python3 main.py submit                 # writes submission/R3_predictions.jsonl for the 150 test questions
python3 main.py submit --split dev     # the 40 dev questions -> output/R3_dev_predictions.jsonl
python3 main.py package                # commit first; then -> output/TEAM-000802_R3.zip, the file to upload
```

`package` makes a fresh `git clone` of the committed project and checks it against the guidelines' page-4
checklist before zipping: only the allowed top-level items; no `.env`, virtual environments, pack data, models,
caches or zips; the lockfile, `.env.example` and a README with every required section; and one valid prediction
line per test question. The team and problem IDs are in `config.json` (`submission`).

`submit` answers each conversation in turn order (later turns keep the earlier constraints) and checks every line
against the pack's format before writing. To score the dev questions with the organisers' own scorer (the pack's
`evaluate.py`, run unchanged), beside their shipped baseline:

```bash
uv sync                                          # once: the scorer needs pandas and numpy (dev-only)
uv run python main.py score --baseline --details # reports in output/reports/
uv run python main.py stress --part holdout      # catalogue-built stress set, scored by the same official scorer
```

The loader accepts `.jsonl`, `.json` and `.csv` catalogues with common column spellings (`id|product_id|sku`,
`title|name`, prices like "₹1,299", stock like "Only 2 left", attributes as a dict, JSON string or flat columns). Bad
rows are skipped and logged. Policy text is split into sections on blank lines; a short first line becomes the
section heading and the id used in citations.

**The provided models are off by default**, because measured on the dev tune half they didn't help (see
Limitations and `FULL_CODE/CHANGES.md`, Task 3). To try them, download them while online with the pack's own script
into `FULL_CODE/models/` (git-ignored), install the libraries by hand (they are not in the lockfile:
`pip-audit` reports advisories for them, one with no fix yet), calibrate, then pass the model folders:

```bash
python3 -I data/pack/download_models.py --set small --dest models   # MiniLM + Qwen2.5-0.5B, about 0.6 GB
uv pip install sentence-transformers torch llama-cpp-python          # not locked; see above
.venv/bin/python main.py calibrate --embed-model models/all-MiniLM-L6-v2 --catalogue data/pack/data/catalogue.jsonl --policy data/pack/data/policy.md --out semantic_config.json
.venv/bin/python main.py chat --embed-model models/all-MiniLM-L6-v2 --llm-model models/Qwen2.5-0.5B-Instruct-GGUF
```

Every model call has a timeout and one retry with back-off for temporary errors (`config.json`, models section);
if a model is slow, busy, fails, or isn't installed, the reply falls back to the rule-based answer.

## How to run the tests

```bash
cd FULL_CODE
uv sync              # once: creates .venv with the pinned dev tools from uv.lock (uv fetches Python 3.12 if needed)
uv run pytest        # 179 tests
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

A tested 90-second walkthrough, with the questions to type and the numbers to quote, is in
`FULL_CODE/docs/demo_script.md`.

The API (`api/`) validates every request (JSON only, size and length limits, allowed fields, server-minted session
ids), refuses requests from other sites, rate-limits each session and client address, and sends security headers,
including a Content-Security-Policy that lets only the page's own script run. It listens on 127.0.0.1 by default.

### Latency and token cost per query

`python3 main.py report` answers all 190 pack questions (40 dev, 150 test; conversation turns in order) and writes a
per-query CSV and a summary to `FULL_CODE/output/reports/`. Measured on a MacBook (Apple silicon, CPU):

| | GroundTruth (measured) | An LLM writes every reply (**estimate**) |
|---|---|---|
| Answered by | rules, 100% of queries | the provided Qwen2.5-0.5B model |
| Latency per query | mean 1.3 ms, p50 0.8 ms, p95 3.2 ms, max 11.5 ms | about 13.4 s |
| Model tokens per query | **0** | about 3,830 in + 120 out |
| All 190 queries | 0.25 s, 0 tokens | about 42 minutes, about 750,000 tokens |

The estimate comes from one measured call of the provided model with the prompt it needs to stay grounded (the
catalogue's allowed product types and attribute values), multiplied by the number of queries; it was not run for
every query. The pack states no token or cost budget; GroundTruth's cost is CPU time only. The per-query CSV also
gives an estimate of the context each reply cites (mean 139 tokens) and of the reply itself (mean 62 tokens), about
four characters per token.

## AI usage

**In the app.** The main path makes no model calls. Two optional fallbacks use the small models provided with the
problem pack, and only when the rule-based parser leaves product words it can't place:

1. **Embedding model** (`groundtruth/models/semantic.py`): maps an unknown shopper word to the nearest catalogue
   product-type word ("jogging" -> "running"), never to a brand, colour or material, and retrieves policy sentences for
   policy questions phrased without policy words. It stays off until `main.py calibrate` has set its thresholds.
2. **Instruct model** (`groundtruth/models/normaliser.py`): turns a messy query into JSON (product type, filters,
   budget). Every field is validated against the catalogue's own vocabulary, and a budget is accepted only if it is one
   of the shopper's own numbers. The model's text never reaches the shopper.

**Status: measured with the real models and switched off.** On the 20 dev questions used for tuning, scored by
the official scorer: rules only 1.000; with the embedding fallback 0.920 (it mapped words to wrong policy sentences
and stopped refusing two unsold items), at 7 ms instead of 0.9 ms a query. The 0.5B instruct model needs the
catalogue's allowed types and attribute values in its prompt (about 3,800 tokens) and took 13.4 s per call on a
laptop CPU, with its JSON cut off at the token limit. Neither helped, so the shipped assistant makes no model calls
(0 tokens per query). The code stays, with timeouts, retries and fall-back to rules, for a larger model or a
different catalogue.

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

* **Official scores are on small samples.** The 40 dev questions were split in half: rules were tuned on one half,
  and the other half (never inspected question by question) scores **0.865**, against the shipped baseline's 0.8275
  on the same 20 questions. All 40 dev score 0.9325 (baseline 0.7315), but that includes the tuned half. A stress
  set built from the catalogue (`main.py stress`: split and misspelt product names, Hinglish, unsold items,
  unlisted facts) scores 0.961 on its held-back half; it was written by us, so it measures robustness to those
  phrasings, not general accuracy. The organisers' 150 test questions are only used to write the submission.
* **The synthetic sets are regression checks only.** They and the fixes were written by the same people, so their
  1.0 scores say nothing about real accuracy; the last clean estimate on them was 0.987.
* **No model in the shipped path.** The provided models were measured and didn't help on dev (see AI usage), so
  words the rules can't place are refused rather than guessed. That is safe but can refuse unusual synonyms.
* **`main.py evaluate`** scores the synthetic sets with our own assumed weights (`config.json`, evaluation
  section); the official score comes from `main.py score`.
* **Known behaviour gaps:** a describing word plus an unknown noun leans towards refusing (safer, but it may
  refuse unusual synonyms); policy-silent questions are recognised only for delivery days and times (Sunday,
  weekend, same day, express, ...), from a word list in `vocabulary.json`; "which is better" between two kinds of
  product gets a clarifying question, because the catalogue has nothing to judge "better" by.
* Latencies are from one laptop. Python threads share one interpreter lock, so serving many users means more
  processes, not more threads (`main.py bench`: about 2,300 queries/s per process on the synthetic catalogue).

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
uv run python main.py shoppers --part holdout2   # shopper-language set (Hinglish, Telugu-English, own words)
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
uv run pytest        # 308 tests (2 need the voice extra)
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
| `groundtruth/catalogue/` | Tolerant catalogue `loader`, and the `index` (inverted postings, set-based filtering, price index, top-n ranking, live updates, typo matching) |
| `groundtruth/policy/` | Policy sections and sentence retrieval |
| `groundtruth/answering/` | `assistant` routing; reply builders for products, listings, refusals and policy; per-user `sessions` |
| `groundtruth/grounding/` | The independent checker shared by the evaluator and the web demo's live check |
| `groundtruth/models/` | Optional fallbacks using the provided models: embedding (`semantic`), whole-vocabulary prompt (`normaliser`, prompt in `models/prompts/`) and the low-token numbered-menu `resolver` |
| `groundtruth/synonyms.py`, `synonyms_cli.py` | Words a model proposed, reviewed by a person (`main.py synonyms`), then answered with no model |
| `groundtruth/storage/` | Session stores behind one interface: `memory` or a shared `sqlite` file |
| `groundtruth/concurrency.py` | The reader-writer lock that lets the catalogue change while questions run |
| `groundtruth/evaluation/` | Scoring and the latency / token report |
| `groundtruth/config.py` | Loads and validates `config.json` and the environment overrides |
| `api/` | Web server: routes (`server`), chat state kept through the session store and retailer insights (`state`), cards and examples (`views`) |
| `web/` | Static UI (no external scripts or fonts) |
| `tools/` | Data and question-set generators, benchmark, embedding calibration |
| `tests/` | Unit and behaviour tests |

The core (`groundtruth/`) does not depend on the web layer and is tested on its own.

### Web demo

![A Hinglish budget question answered with cited product cards](FULL_CODE/docs/ui-chat.png)

Product cards are built only from the cited catalogue records. Tapping a citation flips the card to the exact record or
policy section. Every reply is re-checked by the grounding checker, and the result is shown next to it. The page also
shows the remembered filters and budget, how the last message was read, a side-by-side compare, and a retailer view
(`/insights`) of what was asked for that nothing in the catalogue fits, kept apart from words the assistant couldn't
place (a typo or synonym may be stocked).

A tested 90-second walkthrough, with the questions to type and the numbers to quote, is in
`FULL_CODE/docs/demo_script.md`.

The API (`api/`) validates every request (JSON only, size and length limits, allowed fields, server-minted session
ids), refuses requests from other sites, rate-limits each session and client address, and sends security headers,
including a Content-Security-Policy that lets only the page's own script run. It listens on 127.0.0.1 by default.

**Asking by voice (optional, offline).** A **Speak** button turns a spoken question into text on this computer and
asks it like a typed one; **Read replies aloud** speaks the answer with the computer's own voices, without the citation
tags. Speech-to-text uses faster-whisper with the Whisper "small" model; set it up once, while online:

```bash
cd FULL_CODE
uv sync --extra voice                 # the optional speech library (not needed for anything else)
uv run python main.py voice-model     # the model, about 480 MB, into models/whisper-small/ (git-ignored)
uv run python main.py serve           # the Speak button now appears; no internet needed from here on
```

The page records with the browser's own recorder and posts the recording (WebM, Ogg or MP4, as the browser makes
it) to `/api/transcribe` on the same server, which decodes it with PyAV and transcribes it; it is checked like any
request (same site only, audio types only, at most 15 seconds and 600 KB, rate-limited) and never stored or logged. Without the extra or the model (the model is about 480 MB, so it is not in the submission zip), the
button falls back to the browser's own speech recognition in Chrome or Edge: it needs the internet and sends the audio to
the browser's vendor, so its tooltip says so, and it is not the offline path. In a browser with no speech recognition the
button doesn't appear. Browsers allow the
microphone only on `localhost` or HTTPS, so voice works on the laptop running the demo, not on a phone over Wi-Fi.
Measured with a synthetic Indian-English voice (macOS `say`, not real shoppers): on 12 spoken questions that no
change was based on, 11 gave the same answer as the typed question (the smaller "base" model: 8); transcription takes
about 1–1.5 s. Hindi words are still the weak point ("sabse" can come out as "Sabase"), so the transcript is shown in
the chat before the answer and can be retyped.
Spoken Hindi or Telugu comes out in Latin letters as Whisper hears it, so Hinglish works best.

### Latency and token cost per query

`python3 main.py report` answers all 190 pack questions (40 dev, 150 test; conversation turns in order) and writes a
per-query CSV and a summary to `FULL_CODE/output/reports/`. Measured on a MacBook (Apple silicon, CPU):

| | GroundTruth (measured) | An LLM writes every reply (**estimate**) |
|---|---|---|
| Answered by | rules, 100% of queries | the provided Qwen2.5-0.5B model |
| Latency per query | mean 0.6 ms, p95 1.5 ms | about 13.4 s |
| Model tokens per query | **0** | about 3,830 in + 120 out |
| All 190 queries | 0.11 s, 0 tokens | about 42 minutes, about 750,000 tokens |

The estimate comes from one measured call of the provided model with the prompt it needs to stay grounded (the
catalogue's allowed product types and attribute values), multiplied by the number of queries; it was not run for
every query. The pack states no token or cost budget; GroundTruth's cost is CPU time only. The per-query CSV also
gives an estimate of the context each reply cites (mean 139 tokens) and of the reply itself (mean 55 tokens), about
four characters per token.

### Scaling beyond 5,000 products, and a model that costs almost nothing

Measured with `python3 main.py bench --scale N` (a generated, messy catalogue of N products; one laptop; the same 14
queries before and after, with identical replies on 210 compared). The generated catalogue has only 246 distinct words,
so its queries match more products than a real long-tail catalogue would.

| Products | Mean query before → after | p95 before → after | RAM before → after |
|---|---|---|---|
| 5,000 | 1.4 → 0.27 ms | 2.7 → 0.5 ms | 58 → 60 MB |
| 50,000 | 21 → 1.5 ms | 47 → 3.2 ms | 387 → 414 MB |
| 200,000 | 100 → about 7 ms (6.7 to 8.2 over 3 runs) | 225 → about 14 to 19 ms | 1,485 → 1,585 MB |

The 200,000 figure is steady state: the rank orders and measurement tables are built at start-up (`prepare`, called by
`warm_up`), and the first time a new budget is seen costs a little more than the second. The slowest single query in a
run is still about 160 ms, a one-off first-use cost not yet traced.

What changed: matches are found with set operations (postings, price index, size, stock, exclusions and measurements
all as sets) instead of a check on every product, and the top few are picked without ranking the rest (ids are
split by in-stock and by how much of the query their titles share, then the first of each group is taken from a
pre-sorted order); product names are found through their rarest word, and whole-result checks look at the first 2,000
matches at most (`SCAN_LIMIT`; identical to before whenever there are fewer). `ShoppingAssistant.apply_updates` changes price, stock, products
and removals on a running assistant without a rebuild; questions wait while it runs (a reader-writer lock), and the
tests check the result equals a fresh build. Not done yet: RAM still grows with the catalogue (every product is a Python
object), and past about a million products the index belongs in a database.

**Where chats are kept.** A visitor's chat state (the current search, the last product and listing, the last 30
messages) is about 450 bytes of JSON behind a small `SessionBackend` interface (`groundtruth/storage/`). Two backends
ship: `memory` (this process; the default) and `sqlite` (one file, standard library only, write-ahead logging), chosen
with `python3 main.py serve --sessions sqlite` or `sessions.backend` in `config.json` (file: `sessions.sqlite_path`,
or `GROUNDTRUTH_SESSION_DB`). With SQLite a chat survives a restart, and any number of server processes can share the
file, so a follow-up ("under 2500") may land on a different process and still works (checked with two real server
processes on ports 8101 and 8102, and by a four-process test that writes 600 sessions at once). Both backends keep at
most `server.max_sessions` chats (least recently used first) and forget a chat idle for `sessions.ttl_seconds`.
Measured on one core: a chat turn (load and save) costs about 12 µs in memory and about 69 µs with SQLite, against
0.3 to 7 ms to answer; 100,000 stored sessions take about 70 MB and a random load takes about 23 µs. A shared store
for many machines (Redis, say) needs only the four methods of the interface; it is not built. The retailer-view counters
and the learned-synonym file are still per process.

**Model cost.** The rules answer every pack question with 0 tokens. For the words they can't place, the optional
instruct model is now asked with `ShortlistNormaliser` (`--llm-resolver shortlist`, the default when `--llm-model` is
given): a character-trigram shortlist with no model, then at most two numbered menus of 16 options or fewer (categories,
then the types inside one), answered with one number (4 new tokens at most). On the demo catalogues the prompts are
about 130 tokens in total, whatever the catalogue size, against 421 for the old whole-vocabulary prompt there (3,820 on the
pack's catalogue, as measured earlier; the menu size is capped, so the pack figure would stay near 130; an estimate, the
model was not run here). The shortlist alone puts the right type first for 60 of 62 mistyped type words, at about 3 µs a
word. Not measured: the real model's speed or accuracy on these menus; score it with `main.py score` before turning it on.
`--llm-resolver schema` keeps the old prompt.

**Learning without retraining.** Each word a model resolves is saved as a *proposal* in `data/learned_synonyms.json`.
`python3 main.py synonyms` lists them (most seen first); `synonyms approve WORD [TYPE]` makes one live, `synonyms reject
WORD` drops it. Approved words are answered by the rule path with no model call (`path: synonym` in the chat details and
the retailer view). A proposal is never used until a person approves it, so a wrong mapping can't take effect by itself.

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

**Speech-to-text (optional voice input).** OpenAI's Whisper "small" model, run locally through faster-whisper
(`groundtruth/models/speech.py`), turns a spoken question into text on this computer. It never answers anything: its
text goes through the same rules as a typed question, so it can't add a claim, a price or a product. Its hint text
(shoppers' Hinglish words) is in `vocabulary.json`; the model folder, language and limits are in `config.json`.
Replies are read aloud by the browser's own voices. Without the optional extra, there is no Speak button and no
model.

**AI coding assistants.** Claude (chat) and Claude Code were used to write, refactor and review code, tests and
documentation. The team reviewed every change and can explain the code.

## Pre-built and third-party code

**Written for this hackathon, mostly before Day 1 (30 October 2026).** All the code in this repository was written for
this hackathon by the team, Rushitha Borra and Sushanth Kilari, with the AI assistants named above. Every commit in the
table below came before Day 1 except the last five rows, which were made during the event (30 to 31 October 2026). `git log` has the full history with dates:

| Dates | Commits | Author | What |
|---|---|---|---|
| 2026-10-15 | `ed014d7`, `49effd0`, `b9bc130` | Sushanth Kilari | First grounded assistant, evaluator and synthetic data; negation, comparisons, multi-intent; inverted-index search, typo matching, session store, benchmark |
| 2026-10-16 to 18 | `8898bec`, `b28bb0f`, `1321d50` | Rushitha Borra | Fewer false refusals, tolerant catalogue loading, model fallbacks, held-out sets; compare, retailer view, snapshot dates; retail UI |
| 2026-10-27 to 28 | `d098c6e` to `173642c` | Rushitha Borra | Restructure to the guidelines layout: `FULL_CODE/`, `main.py`, config, logging, lockfile, ruff, licence, README (no behaviour change) |
| 2026-10-28 | `7cb6fd5` to `7640361` | Rushitha Borra | The problem pack: loading, structured claims, submission file, the official scorer beside the baseline, dev split |
| 2026-10-28 | `36eb7d6` to `950f46b` | Rushitha Borra | The pack's answering rules; measuring the provided models; latency and token report; submission zip builder |
| 2026-10-28 | `4411aea` to `552edb4` | Rushitha Borra | API validation and security headers; behaviour gaps; hardening; catalogue stress set and the fixes it found |
| 2026-10-28 | `f7cedbb` | Sushanth Kilari | Read the request body before refusing (flaky connection resets on Windows) |
| 2026-10-28 | `e3901d7` to `4e75a03` | Rushitha Borra | Fixes found while rehearsing the demo; demo script; status document |
| 2026-10-28 | `3abaa8f` to `d000e83` | Sushanth Kilari | Type hints, shorter functions and `tools/` docstrings; a stricter grounding checker (prices bound to their citation, attribute words, policy quotes); strict pack loading; stock wording; retailer view split; handover |
| 2026-10-29 | after `d000e83` | Rushitha Borra | Lint and README fixes; grounding checker no longer flags attribute names |
| **2026-10-30 to 31 (Day 1)** | the five commits after `27c7368`, ending `d9f4762` | Sushanth Kilari with Claude Code | **Made during the event:** faster, set-based catalogue search; live catalogue updates; the low-token numbered-menu resolver and learned-synonym review (off by default, not run with a real model); a session store (memory or SQLite); tests and docs for these. Replies are identical to before on all 150 pack predictions and the dev score is unchanged at 0.9500 |
| **2026-10-31 (during the event)** | the commit "Mend short vowel-slip typos, offer 'did you mean', keep a budget that found nothing" (pull request #1) | Sushanth Kilari with Claude | **Made during the event:** short-word typo repair by one vowel slip ("jeens" -> jeans) with the correction shown in the reply; "Did you mean" offers on refusals; a budget that found nothing stays in force on the next turn; 14 tests (`tests/test_typos_and_budget_memory.py`). Replies to every question in the repo's question sets are unchanged (428 compared before and after) |
| **2026-10-31 (during the event)** | the commit "Offer 'did you mean' only for close slips; note browser speech on the live demo" | Sushanth Kilari | **Made during the event:** tightened the 'did you mean' offer (same first letter, similar length, typed word of at least 5 letters) so real words are not guessed at; notes on the browser-speech fallback. Small change in `catalogue/index.py` and its test |
| **2026-10-31 (during the event)** | the commit "Speak button: browser-speech fallback, and keep a hidden button hidden" | Sushanth Kilari with Claude Code | **Made during the event:** the Speak button was shown even when the server had no speech model (a CSS rule overrode `hidden`) and could not work; it now stays hidden unless voice is available, and uses the browser's own speech recognition when the offline model is not installed. Page and docs only; no answer changes |
| **2026-10-31 (during the event)** | the commit "Do not reuse a budget-missed search for a message with an unplaced word" | Sushanth Kilari with Claude Code | **Made during the event:** after a search that failed only on its budget ("jeans under 1000"), a message with a word the assistant can't place ("hudieee under 1500") was answered with the remembered jeans; it is now refused, as before the budget-memory change. A budget-only follow-up still uses the kept search. Three tests; replies identical on all 210 compared |

**Third-party code.** None is copied into the repository, and the assistant has no runtime dependencies. The
development tools are pinned in `FULL_CODE/uv.lock` (`pip-audit` finds no known vulnerabilities): ruff (MIT),
pytest (MIT), pip-audit (Apache-2.0), and numpy and pandas (BSD-3-Clause), which only the organisers' scorer and
baseline need. The optional model fallbacks, off by default and not in the lockfile, use sentence-transformers
(Apache-2.0), PyTorch (BSD-3-Clause) and llama-cpp-python (MIT). The optional voice input (the `voice` extra, pinned
in `FULL_CODE/uv.lock`, not needed for anything else) uses faster-whisper (MIT) and, through it, CTranslate2 (MIT),
PyAV (BSD-3-Clause), ONNX Runtime (MIT), huggingface-hub and tokenizers (Apache-2.0). Its speech model is OpenAI's
Whisper "small" (MIT), in the CTranslate2 conversion `Systran/faster-whisper-small` (MIT), downloaded by
`main.py voice-model` and kept out of the repository. The problem pack's data is the organisers' (CC BY 4.0) and
stays out of the repository.

## Limitations

* **Official scores are on small samples.** The 40 dev questions were split in half: rules were tuned on one half,
  and the other half (never inspected question by question) scores **0.900**, against the shipped baseline's 0.8275
  on the same 20 questions. All 40 dev score 0.950 (baseline 0.7315), but that includes the tuned half. A stress
  set built from the catalogue (`main.py stress`: split and misspelt product names, Hinglish, unsold items,
  unlisted facts) scores 1.000 on its held-back half; it was written by us, so it measures robustness to those
  phrasings, not general accuracy. The organisers' 150 test questions are only used to write the submission.
* **AI-written shopper-style questions: 0.556 on first contact.** 64 messages in shoppers' style (Hinglish,
  Telugu-English, vague and opinion questions). They were meant to be the brief's Task 4 set from real people, but
  an AI wrote them, so they are **not** a measure of real shoppers, and Task 4 is not done. They were labelled from the catalogue and policy before
  the assistant saw them and scored once: **0.556** on the 53 the official scorer can judge, 0 hallucinated prices.
  The patterns found there were fixed as general rules; a shopper-language part that was never inspected and
  shares no wording with our lists then went from 0.509 to **0.733**. The Task 4 set is now used (0.953 after the
  fixes, which is not evidence) and a fresh one is needed for the next honest measure.
* **Needs and occasions are answered from a hand-written map.** "Ghar pe workout ke liye kya chahiye", "hostel
  shift ho rahi hu", "baarish ka season" name a need, not a product: a map from needs to kinds of product
  (`vocabulary.json`, `needs`) shows one well-rated product of each kind, and a gift with nothing else to go on asks
  who it is for. On 50 such messages (also likely AI-written), judged by criteria written beforehand, the half never used
  for fixing went from 0.06 to **0.68**. Needs outside the map ("daily life easy banani hai", "cute cheezein") are
  still refused.
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
* **Typos:** a misspelt word is mended when it is one vowel slip from exactly one product word ("jeens" -> jeans,
  "shert" -> shirt), and the reply says so (`Showing results for "Jeans" (you typed "jeens")`). A changed consonant
  ("band" for "bank"), a brand, a colour or two equally close words is never guessed: the reply refuses, and may ask
  `Did you mean "Hoodie"?`. Words two or more edits away ("laptpo") are refused with no offer. The rule is strict on
  purpose: a looser one turned "fitness band" into power banks when tried.
* **Budgets:** a budget that matches nothing stays in force for the next turn ("500 ke andar", then "sabse sasti"
  still means under 500) until the shopper gives a new one. Only a failure caused by the budget is remembered; an
  unsold item ("laptop" after "black hoodie") leaves the earlier search as it was.
* Latencies are from one laptop. Python threads share one interpreter lock, so serving many users means more
  processes, not more threads (`main.py bench`: about 2,300 queries/s per process on the synthetic catalogue).

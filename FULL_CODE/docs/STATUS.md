# Project status, gaps and who does what

Team Crew (TEAM-000802) · R3 · checked on 29 October 2026 at `f31c99b` (plus the commit this page is in). Every line
below comes from a command that was run; "not done" means not done. L1 is on 30 October.

## 1. Where we stand

| Measure (official scorer unless noted) | Result |
|---|---|
| Dev check half (20 questions, never tuned on) | **0.900** (shipped baseline 0.8275) |
| All 40 dev questions (includes the half we tuned on) | 0.950 (baseline 0.7315) |
| Stress set from the catalogue, held-back half (81, written by us) | 1.000 |
| **AI-written shopper-style questions** (first Task 4 attempt; 53 officially scored, of 64; not real shoppers, see CHANGES 30 Oct) | **0.556**; after fixes 0.953 (set now used, not evidence) |
| Real shoppers' questions | **not measured** (Task 4 not done) |
| Shopper-language set, second holdout (310, never inspected) | **0.733** (0.509 before the Task 4 fixes; tune half 0.976) |
| Need and occasion requests (50, origin not recorded, treat as AI-written; half held back, judged by criteria written first) | held-back half **0.68** (0.06 before); tune half 0.92 |
| Hallucinated prices, every run | 0 |
| `submission/R3_predictions.jsonl` | 150 lines; **the pack's own validator accepts it** and it covers every test question |
| Tests | 257 pass on Python 3.12 with the voice extra; 255 pass and 2 skip on 3.9 without it |
| Voice input (optional, offline; questions spoken by macOS's Indian-English voice, not real people) | 11 of 12 answered as when typed on a fresh set (Whisper small; base: 8); about 1 s to transcribe |
| Lint, format, `pip-audit` | clean |
| Latency / model tokens | mean 0.6 ms a query / 0 |
| Grounding checker on all 190 pack replies (40 dev, 150 test) | 0 flagged |
| Submission zip (`python3 main.py package`) | checklist passes |

These scores are on small samples and on sets we wrote; the hidden test score may be lower.

## 2. The brief's tasks

| Task | State | Notes |
|---|---|---|
| 1. Restructure to the guidelines layout | done | `FULL_CODE/`, `main.py`, config, logging, lockfile, ruff, LF, licence |
| 2. The organisers' data and format | done | loads the pack strictly; `submit` (the pack's validator accepts it); official scorer via `score`, with the shipped baseline per question type (`--baseline`); tuned on half of dev only |
| 3. The provided models | done | measured; both fallbacks hurt or were too slow, so they are off; every model call has a timeout, retries, a fall-back to rules, logging and mocked tests |
| 4. A fresh held-out set from people who haven't seen the code | **not done** | the 64 messages scored on 29 Oct (0.556) turned out to be AI-written (found 30 Oct); the fixes stand as general rules, but real people's questions are still needed (CHANGES) |
| 5. Known behaviour gaps | done, one part waits | "which sizes", "A or B", policy-silent: done. "Review the describing-word refusal rule" waits for Task 4 (G14) |
| 6. Latency and token-cost report | done | `report`: mean 0.6 ms, 0 tokens; the LLM-every-query estimate is labelled as one |
| 7. Web demo and API security | done, one look left | phone width checked in Chrome's phone emulation (G7); one look on a real phone left |
| 8. Hardening | done | product code: 0 functions over 40 lines, 0 untyped parameters, 0 missing return types, 0 dead code, docstrings on every top-level function, `pip-audit` clean, one documented catch-all at the server boundary; the dev tools meet the same rules (G19) |
| 9. README and submission zip | done | rule-12 sections current; `package` builds the zip from a fresh clone and checks the guidelines' list |
| Extra | done | offline voice input (`--extra voice`, `voice-model`; CHANGES); `stress` and `shoppers` sets, `docs/demo_script.md`, `docs/code_tour.md`, retailer view |

## 3. Gaps against the brief and the organisers' guidelines

Priority: **P1** before L1 (30 Oct), **P2** before later rounds, **P3** nice to have.

| # | Gap | Rule it breaks | Evidence | Owner | Priority |
|---|---|---|---|---|---|
| G1 | Two commits not pushed | the zip must come from a clone of what's on GitHub | `git status`: ahead 2 | Rushitha | done (`4e75a03`) |
| G2 | README facts out of date: test count says 179 (it is 182); the pre-built-code table stops at `8d8e35d` (20 later commits, including Sushanth's 4, are missing); third-party list omits numpy and pandas (BSD-3, dev only) | rule 12, fair play (declare pre-Day-1 code) | `grep` on README; `git log 8d8e35d..HEAD` | Claude Code | done |
| G3 | 25 `# fmt: skip` and 1 `# noqa: E731`; the `noqa` hides a lambda in `api/state.py` that computes percentiles differently from the cost report | rule 1: fix warnings, don't switch them off; no duplicated logic | `grep` | Claude Code | done: 0 suppressions now, one shared `percentile` |
| G4 | **Done 28 Oct.** Was 9 product functions over 40 lines (`make_handler` 97, `parse_settings` 82, `CatalogueIndex.__init__` 63, `summarise` 62, `product_answer` 51, `build_assistant` 51, `build_examples` 48, `check` 46, `no_match` 45); now none is over 40 (longest 33) | rule 1: short, single-purpose functions | AST count; 182 tests; 328 reply and score entries identical before and after | Sushanth with Claude Code | done |
| G5 | **Done 28 Oct.** Was 37 parameters and 15 return types without type hints in product code; now 0 and 0 | rule 1: type hints | AST count; 182 tests; ruff clean | Sushanth with Claude Code | done |
| G6 | **Done 28 Oct.** Was 48 functions in `tools/` without docstrings; now 0. Only docstrings changed: the code is identical once they are ignored | rule 12: docstrings on key functions | AST count and AST comparison with the committed files | Sushanth with Claude Code | done |
| G7 | Web demo at phone width | brief task 7 | **Checked 29 Oct in Chrome's phone emulation** (390×844 and 360×740): the whole demo script, a card flip, compare, a citation and `/insights`, 15 states each, 0 with horizontal overflow. Found and fixed a raw `sub_category: Kurtas` chip. On 30 Oct Sushanth opened the page from a desktop browser at the laptop's Wi-Fi address (a layout look, not a phone); a look on a real phone is still open | Sushanth: one look on a real phone | P2 |
| G8 | Zip name: `TEAM-000802_R3.zip` or `000802_R3.zip`? Is `.gitattributes` allowed at the top level? | submission rules | guidelines list only README, .gitignore, LICENSE, submission/, FULL_CODE/ | **Rushitha** (ask organisers) | **P1** |
| G9 | Round-1 deck showed old synthetic numbers (0.987, 88 tests, 0.8 ms) and "Rushita Bora" | honest reporting | deck text | Claude Code; Rushitha uses it | done: `~/Documents/r3_megahack/CREW_R3_deck_updated.pptx` (+ `.pdf`) shows 0 hallucinated prices, 0.900, ~1 ms, 240 tests, 0.635 → 0.900, stress 0.885 → 1.000; outside the repo; original untouched. Check the 60-second recording slides 3 and 7 mention exists |
| G10 | Task 4: no independent held-out set | brief rule 4, task 4 | | Sushanth collects from real people | **open**: the 64 scored on 29 Oct were AI-written (0.556 on those); a real set with a record of who typed each is needed |
| G11 | Offline readiness on the event laptop | demo must run offline | **Checked 29 Oct on this laptop:** the app makes no outgoing connections and loads nothing from the internet; served by bare system Python 3.9 with no virtual environment, all 5 routes return 200 and the 9 demo-script steps, typed in order, give the replies the script promises. **30 Oct, Sushanth's Windows laptop, real pack:** strict loading accepted 5,000 products; the demo script's typed steps (1 to 7 and 9) gave the replies it promises, each passing the grounding check; Sushanth then ran the server with Wi-Fi off at `http://127.0.0.1:8000` and reported it working (his report, not checked by Claude). Whether that laptop is the event laptop is not recorded | **Sushanth**: fresh clone + pack + Wi-Fi off on the event laptop | **P1** |
| G12 | "Every team member must be able to explain the code" | fair play | | both: `docs/code_tour.md` (about an hour, or "quiz me" with Claude Code) | **P1, before L1** |
| G13 | Commits `d098c6e` and `c8bd0a8` don't run on their own (a paste split `git add`); later commits fix it | clean history | documented in CHANGES.md | nobody (leave as is) | done |
| G14 | Real shoppers' language. **Done 29 Oct, from Task 4 and a second batch of 50 need requests:** the pack's rules 6.2 and 6.3 in full, filler words, values read against the kind's own attribute, the most specific kind, "X or Y", sizes, refinements, policy how-to, unsold products named alone; needs and occasions mapped to kinds of product, gifts answered with a question. Fresh shopper holdout 0.509 → 0.733; need holdout 0.06 → 0.68; dev check 0.865 → 0.900; stress holdout 0.961 → 1.000. **Still weak:** needs outside the hand-written map | `main.py shoppers --part holdout2`; Task 4 | Claude Code; a fresh batch to measure | P2 |
| G15 | L1 payment for R3 and R5 | event | application tracker | Rushitha | done (29 Oct) |
| G16 | The new attribute-word rule flagged 36 of 190 real pack replies: attribute *names* ("tell me a brand, colour or budget", "does not list a water_resistance_rating") matched attribute values such as "color pencil" | the demo's grounding badge must not fail good replies | HANDOVER.md step A.5 | Claude Code | done: names are not checked as values; 0 of 190 flagged; regression test |
| G17 | HANDOVER 3C: raw field names in 43 of 150 test answers ("The water_resistant of ... is False"); 3 fan brands (Havells, Bajaj, Usha) listed fans instead of refusing | readable, honest replies | submission scan; probes | Claude Code | done: 3 left, all word-for-word policy quotes; 124 Indian brands refused by name; 13 refusals that cite products need no change (the official scorer gives a correct refuse full credit) |
| G18 | Demo script step 2 failed when typed after step 1: "cotton kurti for women" kept step 1's "blue running shoes" and refused; the retailer view filed iPhone under "words I couldn't place" | the demo | phone run of the script | Claude Code | done: a new kind of product starts a new search; known outside brands count as demand |
| G19 | The data generators and dev tools had 53 untyped parameters, 15 missing return types and 6 functions over 40 lines (longest 179) | rule 1 (G4/G5 covered product code only) | AST count, 29 Oct | Claude Code | done: 0, 0 and 0 across the whole codebase; every generated file, the stress set and the shopper set are byte-identical before and after |
| G20 | 73 product-code functions had no docstring, nearly all private helpers (`_route`, `_send`, config readers) | rule 12: docstrings on key functions | AST count, 29 Oct | Claude Code | done: 66 added (only docstrings changed, checked by comparing syntax trees); 7 tiny nested helpers left |

## 4. What is left, in order

**Before L1 (30 Oct)**

| # | What | Who | Gap |
|---|---|---|---|
| 1 | Send the organisers the three questions (zip name, `.gitattributes`, deadline and upload place) | Rushitha | G8 |
| 2 | Check the 60-second recording the deck mentions exists and plays | Rushitha | G9 |
| 3 | Learn to explain the code: `docs/code_tour.md` together | Rushitha + Sushanth | G12 |
| 4 | `git pull --rebase origin main`; on the event laptop: fresh clone, copy the pack into `FULL_CODE/data/pack/`, Wi-Fi off, `python3 main.py serve`, go through `docs/demo_script.md`; write down anything that fails | Sushanth | G11 |
| 5 | Open the demo on a real phone (same Wi-Fi, `serve --host 0.0.0.0`, the laptop's IP:8000): chat, a card flip, compare | Sushanth | G7 |
| 6 | Upload `FULL_CODE/output/TEAM-000802_R3.zip` (not the `_safe_` backup) | Rushitha | |

**After L1**

| # | What | Who | Gap |
|---|---|---|---|
| 7 | Needs outside the hand-written map: try the provided embedding model, narrowly, and keep it only if it measurably helps | Rushitha + Claude Code | G14 |
| 8 | 50+ questions typed by real people who haven't seen the code (the earlier sets were AI-written), with a record of who typed each, scored once | Sushanth + friends | G10 |

**Future scope: a public voice demo.** Voice input runs only where the optional speech library and the Whisper small model (about 480 MB) are installed, and it needs roughly 1 GB or more of RAM. The free Render demo (https://groundtruth-shop-assistant.onrender.com, synthetic catalogue, 512 MB) therefore has no Speak button; the live site deployed on 30 Oct showed "Voice input is off" in its log. Voice is demonstrated on `http://127.0.0.1:8000` with the real pack. Hosting it publicly would need a host with more memory (a paid Render plan of 2 GB, or a free Hugging Face Space with a Dockerfile) or a tunnel from the laptop; none of these has been set up.

If the organisers ask for `000802_R3.zip` or no `.gitattributes`, Claude Code changes `tools/make_submission_zip.py`.

## 5. Habits

* Before anything: `git pull --rebase origin main`; say in the group chat when you push; never commit `.env`,
  `data/pack/`, `models/` or `output/`.
* After any change: `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`, and with the pack
  `uv run python main.py score --part all` (must stay at least 0.950), `uv run python main.py stress --part tune`
  (0.9996) and `uv run python main.py shoppers --part tune` (0.9761). Rebuild the zip after the last push.

## 6. Learning the code (for both of us)

Start with `docs/code_tour.md`: one message traced through the code, the likely judge questions with where to
point, and 20 minutes of exercises. The reading order below is for going deeper.

Read in this order; each file is short:

1. `FULL_CODE/main.py`: every command.
2. `groundtruth/understanding/parser.py`: how a message becomes intents and a search spec.
3. `groundtruth/catalogue/index.py`: search, filters, ranking.
4. `groundtruth/answering/assistant.py`: the decision between answer, clarify and refuse.
5. `groundtruth/answering/response.py`: claims, the reason no price can be invented.
6. `groundtruth/grounding/checker.py`: the independent check shown as the badge.
7. `groundtruth/evaluation/submission.py`: how claims become the submission file.

Then try: `python3 main.py chat --catalogue data/pack/data/catalogue.jsonl --policy data/pack/data/policy.md --meta`.

## 7. Rules we keep

* Never tune on the test questions or the dev check half; if a held-out set is used to fix something, write down
  that it is contaminated (`FULL_CODE/CHANGES.md`).
* Every number in the README, deck or demo comes from a command we ran.
* Python 3.9 must keep working (`/usr/bin/python3 -m unittest discover -s tests`).
* Pull before starting, push when done, rebuild the zip after the last commit.

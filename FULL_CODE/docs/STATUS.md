# Project status, gaps and who does what

Team Crew (TEAM-000802) · R3 · checked on 29 October 2026, after rebasing onto Sushanth's `d000e83`. Every line below comes from a command
that was run; "not done" means not done.

## 1. Where we stand

| Measure (official scorer unless noted) | Result |
|---|---|
| Dev check half (20 questions, never tuned on) | **0.865** (shipped baseline 0.8275) |
| All 40 dev questions (includes the half we tuned on) | 0.9325 (baseline 0.7315) |
| Stress set from the catalogue, held-back half (81, written by us) | 0.961 |
| Hallucinated prices, every run | 0 |
| `submission/R3_predictions.jsonl` | 150 lines; **the pack's own validator accepts it** and it covers every test question |
| Tests | 214 pass on Python 3.9 and 3.12 |
| Lint, format, `pip-audit` | clean |
| Latency / model tokens | mean 0.7 ms a query / 0 |
| Grounding checker on all 190 pack replies (40 dev, 150 test) | 0 flagged |
| Submission zip (`python3 main.py package`) | checklist passes |

These scores are on small samples and on sets we wrote; the hidden test score may be lower.

## 2. The brief's tasks

| Task | State | Notes |
|---|---|---|
| 1. Restructure to the guidelines layout | done | `FULL_CODE/`, `main.py`, config, logging, lockfile, ruff, LF, licence |
| 2. The organisers' data and format | done | loads the pack; `submit`; official scorer via `score`; tuned on half of dev only |
| 3. The provided models | done | measured; both fallbacks hurt or were too slow, so they are off |
| **4. A fresh held-out set from people who haven't seen the code** | **not done** | needs people (see section 4) |
| 5. Known behaviour gaps | done, one part waits | "review the describing-word refusal rule" needs Task 4's questions |
| 6. Latency and token-cost report | done | `report` |
| 7. Web demo and API security | done, one part waits | phone width not checked in a real browser |
| 8. Hardening | done | G4–G6 closed on 28 October (`CHANGES.md`, "Code quality: ...") |
| 9. README and submission zip | done, gaps below | see G2 |
| Extra: stress set, demo script | done | `stress`, `FULL_CODE/docs/demo_script.md` |

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
| G7 | Web demo at phone width | brief task 7 | **Checked 29 Oct in Chrome's phone emulation** (390×844 and 360×740): the whole demo script, a card flip, compare, a citation and `/insights`, 15 states each, 0 with horizontal overflow. Found and fixed a raw `sub_category: Kurtas` chip. Not yet tried on a real phone | Sushanth: one look on a real phone | P2 |
| G8 | Zip name: `TEAM-000802_R3.zip` or `000802_R3.zip`? Is `.gitattributes` allowed at the top level? | submission rules | guidelines list only README, .gitignore, LICENSE, submission/, FULL_CODE/ | **Rushitha** (ask organisers) | **P1** |
| G9 | Round-1 deck shows old synthetic numbers (0.987, 88 tests, 0.8 ms); slide 1 has "Rushita Bora" and "Retail • Biotechnology" | honest reporting | deck text | Rushitha decides | updated copy made: `~/Documents/r3_megahack/CREW_R3_deck_updated.pptx` (+ `.pdf`), outside the repo; original untouched |
| G10 | Task 4 not done: no independent held-out set | brief rule 4, task 4 | | **Sushanth + friends** collect; Rushitha + Claude Code score once | **P1** |
| G11 | Offline readiness on the event laptop | demo must run offline | **Checked 29 Oct on this laptop:** the app makes no outgoing connections and loads nothing from the internet; served by bare system Python 3.9 with no virtual environment, all 5 routes return 200 and the 9 demo-script steps, typed in order, give the replies the script promises. Not yet tried on the event laptop | **Sushanth**: fresh clone + pack + Wi-Fi off on the event laptop | **P1** |
| G12 | "Every team member must be able to explain the code" | fair play | | both: read `docs/code_tour.md` together and do its section 4 | **P1, tonight** |
| G13 | Commits `d098c6e` and `c8bd0a8` don't run on their own (a paste split `git add`); later commits fix it | clean history | documented in CHANGES.md | nobody (leave as is) | done |
| G14 | Known behaviour limits: unusual synonyms are refused; "policy is silent" covers delivery days/times only; refusals for unlisted facts score 0.75 on the stress holdout (not inspected) | | CHANGES.md | Claude Code, after Task 4 data | P2 |
| G15 | L1 payment for R3 and R5 | event | application tracker | Rushitha | done (29 Oct) |
| G16 | The new attribute-word rule flagged 36 of 190 real pack replies: attribute *names* ("tell me a brand, colour or budget", "does not list a water_resistance_rating") matched attribute values such as "color pencil" | the demo's grounding badge must not fail good replies | HANDOVER.md step A.5 | Claude Code | done: names are not checked as values; 0 of 190 flagged; regression test |
| G17 | HANDOVER 3C: raw field names in 43 of 150 test answers ("The water_resistant of ... is False"); 3 fan brands (Havells, Bajaj, Usha) listed fans instead of refusing | readable, honest replies | submission scan; probes | Claude Code | done: 3 left, all word-for-word policy quotes; 124 Indian brands refused by name; 13 refusals that cite products need no change (the official scorer gives a correct refuse full credit) |
| G18 | Demo script step 2 failed when typed after step 1: "cotton kurti for women" kept step 1's "blue running shoes" and refused; the retailer view filed iPhone under "words I couldn't place" | the demo | phone run of the script | Claude Code | done: a new kind of product starts a new search; known outside brands count as demand |

## 4. What only people can do

1. **Task 4 questions (G10).** 50+ questions by people who haven't seen the code, written the way shoppers type
   (Hinglish, Telugu-English, short, misspelt), about the pack's store. A good mix: about 20 asking a product's
   price, stock or a detail; 10 vague ones that match several products; 10 for things the store doesn't sell; 10
   about returns, delivery, warranty, payment, cancellation, including some the policy doesn't cover. Collect them
   in a shared sheet with one question per row; **don't show them the code, the dev questions or our replies.**
   Expected answers are then worked out by filtering the catalogue, not by asking the assistant, and the set is
   scored once.
2. **Organisers (G8).** Zip name format; `.gitattributes` at the top level; submission deadline and upload place.
3. **Phone and offline checks (G7, G11).**

## 5. Who does what

**Rushitha (with Claude Code)**
* Now: push (`git push origin main`), then `cd FULL_CODE && python3 main.py package` (G1).
* Ask the organisers (G8); L1 payment (G15); decide on the deck (G9).
* Claude Code: README facts (G2), remove the suppressions and the duplicated percentile code (G3), then score Task 4
  once its questions arrive and fix what it shows (G10, G14). Rebuild the zip after the last commit.

**Sushanth**
* Before anything: `git pull --rebase origin main`; say in the group chat when you push; never commit `.env`,
  `data/pack/`, `models/` or `output/`.
* Offline dry run on the event laptop (G11): fresh `git clone`, copy the pack into `FULL_CODE/data/pack/`, turn off
  Wi-Fi, run `python3 main.py serve`, go through `FULL_CODE/docs/demo_script.md`. Write down anything that fails.
* Phone check (G7): open the demo on a phone (same Wi-Fi, `--host 0.0.0.0`, then the laptop's IP:8000); try the
  chat, a product card flip and compare; screenshot anything that overflows.
* Collect the Task 4 questions from friends who haven't seen the code (G10).
* Type hints (G5), splitting long functions (G4) and `tools/` docstrings (G6): done on 28 October, one commit per
  file. Still to do before pushing: with the pack in `FULL_CODE/data/pack/`, run
  `uv run python main.py score --part all` (must stay 0.9325) and `uv run python main.py stress --part tune`
  (must stay 0.9996). Run with the pack on 29 October: unchanged (0.9325, 0.9996; holdout 0.9611).

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

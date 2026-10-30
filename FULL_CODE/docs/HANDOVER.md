# Handover: what Sushanth and Claude did, and what Rushitha and her Claude do next

Written 28 October 2026 for the Claude Code session working with Rushitha, and for Rushitha. Team Crew
(TEAM-000802), problem R3 (GroundTruth, the shopping assistant that doesn't lie). Read `docs/STATUS.md` and
`CHANGES.md` too; this page only adds what changed after them and what is still open. Every number below comes from a
command that was run; "not run" means not run.

## 0. Where every item below stands (29 October, at `f31c99b`)

Sections 1–6 are Sushanth's handover of 28 October, kept as written. Everything in them has been pushed and run with
the pack. Measured on 29 October (latest): 240 tests pass on Python 3.9 and 3.12; dev 0.950 (check half 0.900);
stress holdout 1.000; AI-written shopper-style questions 0.556 (meant as Task 4; not real shoppers, see CHANGES 30 Oct); 0 hallucinated prices; the
grounding checker flags 0 of 190 pack replies.

| Item | State |
|---|---|
| 3A.1–2 tests, ruff, scores must not drop | done: nothing dropped; later work raised them (above) |
| 3A.3 strict loading on the real catalogue | done: accepts all 5,000 products |
| 3A.4 real stock values | done: only `in_stock`, `out_of_stock`, `low_stock`, so the "ships in N days" change affects nothing |
| 3A.5 new grounding rules on the real pack | done: they flagged 36 of 190 good replies (attribute names read as values); fixed, now 0 (STATUS G16) |
| 3A.6 compare the submission; `package` | done: the validator accepts all 150 lines; the zip is rebuilt after every push |
| 3A.7 Python 3.9 and 3.12 | done |
| 3B G1, G2, G3, G9, G15 | done |
| 3B G8 ask the organisers | **open** (Rushitha) |
| 3B G12 explain the code | **open** (both): `docs/code_tour.md` |
| 3C.1 raw attribute names in answers | done: 43 of 150 → 3, all word-for-word policy quotes (STATUS G17) |
| 3C.2 unlisted brands | done: 124 brands refused by name (STATUS G17) |
| 3C.3 refusals that cite products | no change needed: the official scorer gives a correct refuse full credit |
| 3C.4 synonyms, policy-silent topics | partly done (STATUS G14): shopper-language fixes; words nobody prepared for wait for Task 4 |
| 4 phone check (G7) | checked in Chrome's phone emulation; one look on a real phone left (Sushanth) |
| 4 offline dry run (G11) | checked on Rushitha's laptop with bare Python 3.9; the event laptop with Wi-Fi off is left (Sushanth) |
| 4 Task 4 questions (G10) | not done: the 64 scored (0.556) were AI-written; real people's questions still needed |

Also found and fixed on 29 October, beyond this page: demo step 2 failed when typed after step 1, and the retailer
view filed iPhone as an unplaced word (STATUS G18). The full list of what is left is STATUS.md section 4.

## 1. State of `main` (28 October; all of it has been pushed since)

* Local `main` on Sushanth's machine is **7 commits ahead of `origin/main`** (not pushed yet): `d64240a`, `223b83e`,
  `b59342a`, `63aca43`, `0bf0e1b`, `e4fbdf8`, `728c9f9`. Sushanth's Claude never pushes; it gives Sushanth the commands. After the
  push, run `git pull --rebase` before anything else.
* **206 tests pass** (Python 3.13 only), ruff and the formatter are clean. The synthetic question sets still score 1.0,
  which only shows nothing regressed (the sets were written with the code).
* **Not run since these changes, because the pack is not on Sushanth's machine:** `score --part all` (was 0.9325),
  `stress --part tune` (was 0.9996), `submit`, `package`, and the tests on Python 3.9 and 3.12. Those are yours.

## 2. What Sushanth and Claude did (28 October)

| What | Commits | Pushed? |
|---|---|---|
| Pulled and reviewed Rushitha's 3 commits (name handling, demo script, STATUS.md). The new `R3_predictions.jsonl` differs from the old one in 7 reply wordings only: 0 actions, 0 citations or prices changed | n/a | n/a |
| Fixed the flaky `test_other_sites_and_other_methods_are_refused`: the server answered refusals (403/404/405) without reading the request body, so Windows sometimes reset the connection. It now reads the body first; a regression test repeats the refusals 300 times | `f7cedbb` | yes |
| **G5, G4, G6** from STATUS.md: 0 untyped parameters (was 37), 0 functions without a return type (was 15), 0 functions over 40 lines (was 9, longest 97), 0 undocumented `tools/` functions (was 48). One commit per file. Proved behaviour-neutral: 328 reply/score/settings entries identical before and after on the synthetic sets, 182 tests, same speed | 34 per-file commits ending `e587bf9`, docs `cd8dcb3` | yes |
| An audit of the whole project against the R3 brief (section 5) | n/a | n/a |
| **Review fix 1: stock parser.** "ships in 3 days", "ships tomorrow", "dispatched in 24 hours", "available in 5 days" now leave stock **unknown**; "in stock" and "Only 5 left" still count. The old test that expected `True` was changed | `d64240a` | no |
| **Review fix 2: price checker.** Each ₹ amount must belong to the citation next to it; the shopper's own numbers count only as a budget ("under ₹500"). 3 of 7 new tests fail on the old checker | `223b83e` | no |
| **Review fix 3: retailer view.** Requests it understood but could not fill ("not stocked") are separate from words it could not place ("not understood", e.g. a typo); the CSV has a `kind` column | `b59342a` | no |
| **Review fix 4: strict loading.** The pack catalogue is loaded strictly (bad row or duplicate id stops the run) and must hold **exactly 5000 products** (`catalogue.pack_expected_products` in `config.json`; Sushanth confirmed 5,000) | `63aca43` | no |
| **Checker: attribute words and policy quotes.** A colour, material or brand word must come from the record cited in the same sentence (or the shopper's words); a sentence cited to a policy section must be a word-for-word sentence of it. 6 of 8 new tests fail on the previous checker; no false flags on 325 synthetic replies or about 16,000 generated queries | `e4fbdf8` | no |
| Docs: new section in `CHANGES.md`, README test count (206), STATUS rows G4 to G6 marked done | `0bf0e1b`, `728c9f9` | no |

## 3. What you do next (in this order)

**A. After Sushanth pushes, on your machine (you have the pack):**

1. `git pull --rebase`, then from `FULL_CODE`: `uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .`
2. `uv run python main.py score --part all` (must stay **0.9325**) and `uv run python main.py stress --part tune` (must stay
   **0.9996**; the holdout was 0.961). If a score drops, find which of the commits above caused it before touching anything.
3. `uv run python main.py submit`. It is the first run with strict loading on the real catalogue. If it stops with "holds N
   products, expected 5000" or a bad-row message, read it: either the pack really differs (then fix
   `catalogue.pack_expected_products`) or a row is bad (a real finding, do not silence it).
4. **Check the real stock values.** Run this and read the list; if values like "ships in N days" exist, those products now
   say "no stock status in the catalogue" (honest, but decide if that is what you want):
   `uv run python -c "import json,collections; print(collections.Counter(json.loads(l).get('stock_status') for l in open('data/pack/data/catalogue.jsonl',encoding='utf-8') if l.strip()).most_common(20))"`
5. **Check the new grounding rules against the real pack.** An attribute value that is also an ordinary word could wrongly flag
   a good reply (the demo's "failed grounding check" count must be 0). Save this as a scratch file in `FULL_CODE`, do not commit it,
   and run it with `PYTHONPATH=. python scratch_check.py`. It must print `0 of N replies flagged`; if not, read each flagged
   reply and decide whether the reply or the rule is wrong:

   ```python
   from groundtruth.answering import Session
   from groundtruth.builder import build_assistant
   from groundtruth.config import get_settings
   from groundtruth.evaluation.questions import conversations, load_questions
   from groundtruth.grounding import Grounding

   paths = get_settings().paths
   assistant = build_assistant(paths.pack_catalogue, paths.pack_policy)
   grounding = Grounding(assistant.index, assistant.sections)
   flagged = total = 0
   for questions in (paths.dev_questions, paths.test_questions):
       for turns in conversations(load_questions(questions)):
           session, said = Session(), []
           for q in turns:
               said.append(q.text)
               reply = assistant.ask(q.text, session)
               problems = grounding.check(reply.text, " ".join(said))["problems"]
               total += 1
               if problems:
                   flagged += 1
                   print(q.question_id, "|", q.text, "|", problems[:2])
   print(f"{flagged} of {total} replies flagged by the grounding checker")
   ```

6. Compare the new `submission/R3_predictions.jsonl` with the committed one (same question ids; actions and citations should
   not change; wording changes are expected only where stock was parsed differently). Then `uv run python main.py package`
   **after the last commit**, and check the zip.
7. Run the tests on **Python 3.9 and 3.12** (the README says 3.9 works; only 3.13 has been used since the refactor).

**B. Your own open items from STATUS.md** (unchanged by Sushanth's work):

* **G1** is stale: the two commits it mentions are already on `origin/main`. Replace it with the push in section 1.
* **G2** README: the test count is now fixed (206), but the pre-built-code table still stops at `8d8e35d`, and the third-party
  list omits numpy and pandas (BSD-3, dev only).
* **G3** 28 `# fmt: skip` and 1 `# noqa: E731` remain; the `noqa` hides a lambda in `api/state.py` (`Insights.summary`) that
  computes percentiles differently from `cost_report._percentile`. Remove the duplicate.
* **G8** ask the organisers: zip name (`TEAM-000802_R3.zip` or `000802_R3.zip`) and whether `.gitattributes` may sit at the top
  level. **G9** the Round-1 deck still has old synthetic numbers and the name "Rushita Bora". **G15** L1 payment for R3 and R5.
* **G12** walk Sushanth through the core code (STATUS section 6); Sushanth will be asked to explain it.

**C. Open issues found by the audit, not fixed (your decision):**

1. **Raw attribute names in answers.** 43 of the 150 test answers say things like "The water_resistant of the ... is False" and
   "does not list a induction_compatible". True, but it reads badly. A fix would show "water resistant" and "No" in the text
   while keeping the field names in `stated_facts`. Check that the official scorer does not rely on the exact text first.
2. **Unlisted brands.** Only brands in `text.py`'s `EXTERNAL_BRANDS` are refused ("nike", "samsung"). An unlisted brand
   ("Sparx running shoes") is ignored and the reply shows other brands' shoes, sometimes with a small note. Nothing false is
   said, but a refusal would be more honest.
3. **13 of the 150 refusals cite products** ("does not list X for this product [SKU]"). Grounded, but confirm the official
   scorer does not penalise a `refuse` that carries citations.
4. Known limits already in `CHANGES.md`: unusual synonyms are refused; "policy is silent" covers delivery days and times only.

## 4. What only people can do (Sushanth)

Phone check of the web demo (G7), the offline dry run on the event laptop (G11), and collecting 50+ questions from friends
who have not seen the code (G10; then you and your Claude score them once, never tuning on them).

## 5. Audit against the R3 brief (summary)

Every required output exists and was exercised: a CLI and a web chat; citations on every claim (150 of 150 test answers carry
them; the structured fields match the text); explicit refuse and clarify paths (67 answers, 34 clarifications, 49 refusals);
an evaluator with a heavy hallucinated-price penalty and the organisers' scorer wrapped as `main.py score`; per-query latency
and tokens (0 model tokens); and the multi-turn budget stretch (checked in code: every cited product fits the budget and
material). The one real weakness was in the safety net, not the replies: the checker was weaker than the README claimed. It is
fixed above, but only the real pack can confirm the new rules.

## 6. Rules and habits that keep us out of trouble

* Run commands from `FULL_CODE` (`main.py` lives there); git commands from the repo root. Paths in `git add` are relative to
  where you are.
* Never tune on the test questions or on the dev check half; note any contamination in `CHANGES.md`.
* Every number in the README, the deck or the demo must come from a command we ran.
* Python 3.9 must keep working. Pull before you start, push when done, rebuild the zip after the last commit.
* The pack (`FULL_CODE/data/pack/`), models and `output/` are git-ignored: never commit them.
* Sushanth's Claude does not push or commit on its own; it gives Sushanth the commands. Agree the same with yours.

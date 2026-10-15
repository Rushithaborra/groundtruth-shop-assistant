# Groundtruth Shop Assistant

A conversational shopping assistant over a fixed product catalogue that **cites every claim and refuses when the
catalogue can't support an answer**. Pure Python standard library, fully offline, zero LLM calls.

## Quick start

```bash
python data/generate_synthetic.py     # writes data/catalogue.jsonl, policy.txt, test_questions.jsonl (stand-ins)
python chat.py --meta                 # interactive CLI; --meta shows action, latency, token estimates
python evaluate.py --verbose          # scores + reports/summary.json + reports/query_report.csv
python -m unittest discover -s tests

python data/generate_hard.py          # adversarial questions (typos, negation, comparisons, multi-turn, ...)
python evaluate.py --questions data/hard_questions.jsonl --verbose
python data/generate_scale.py         # 5,000 messy products in data/scale/ (git-ignored)
python evaluate.py --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt                    --questions data/scale/hard_questions.jsonl
```

Swap in the real data with `--catalogue`, `--policy` and `--questions` on `chat.py` / `evaluate.py`.
`.jsonl`, `.json` and `.csv` catalogues are accepted (`id|product_id|sku`, `title|name`, `price`, `in_stock|stock_status`,
`rating`, `attributes` as a dict or JSON string). Policy text is split into sections on blank lines; a short first line
becomes the heading and the section id used in citations (`[policy:returns]`).

## How it works

Every reply sentence is assembled from catalogue fields or extracted verbatim from the policy text, then tagged with a
citation (`[P0012]`, `[policy:shipping]`). There is no free-form generation, so a price that isn't in the catalogue
cannot be produced.

| Situation | Behaviour |
|---|---|
| Question about one product that is unambiguous | Answer from catalogue fields |
| "the blue running shoe" when several match | **Clarify**: list candidates with their distinguishing attributes, no prices |
| Product/word not in the catalogue ("iPhones") | **Refuse** |
| Constraints nothing satisfies ("cotton shirts under ₹100") | **Refuse**, and name the closest cited alternative |
| Returns / shipping / warranty | Quote the matching policy sentences, cite the section |
| Policy question the document doesn't cover | **Refuse** |
| Follow-ups ("under ₹2,000, and I already said cotton", "what's the price?") | Session keeps type, filters, budget, last product |

Pipeline: `understand.py` (budget, size, attribute filters, intent, typo/Hinglish handling) -> `index.py` (hard-constraint
filtering and ranking) -> `assistant.py` (answer / clarify / refuse). The attribute vocabulary (colours, brands, materials, ...)
is derived from the catalogue itself, so new catalogues need little configuration. Hinglish words and synonyms live in
`shopassistant/text.py` (`SYNONYMS`, `STOPWORDS`); extend them for the real queries.

## Scoring (`evaluate.py`)

The grounding checker re-derives what each citation licenses from the raw catalogue; it does not trust the assistant's
own bookkeeping.

* **Groundedness**: share of replies with no unsupported claim: citations resolve, every ₹ amount belongs to a cited
  product, cited policy section or the user's own query, other numbers appear in the cited sources, every product named
  is cited, every factual sentence carries a citation.
* **Helpfulness** (answerable questions): right product/policy cited, results satisfy the filters, required facts present.
* **Refusal accuracy**: answer / clarify / refuse matches the expected behaviour (confusion matrix in `summary.json`).
* **Hallucinated prices** each subtract 0.10 from the final score. `final = 0.35*grounded + 0.35*helpful + 0.30*refusal - 0.10*hallucinated`.
  These weights are my assumption; change them at the top of `evaluate.py` to match the official rubric.
* **Latency and token report**: per query in `reports/query_report.csv`, aggregates in `summary.json`. Token counts are
  estimates (about 4 characters per token) of what an LLM would be given (query plus cited sources) and produce; this
  assistant makes no LLM calls, so real cost is zero. Use `--price-in/--price-out` to price it as if it were.

## Serving many users

* `ShoppingAssistant` and `CatalogueIndex` are read-only after start-up (about 35 MB for 5,000 products), so one instance
  is safe to share across threads. Per-user state lives only in a `Session` (a few hundred bytes).
* `SessionStore` keeps sessions per user id: bounded (LRU), expiring (TTL), thread-safe, and serialises requests from the
  same user while different users run in parallel. Use `store.ask(assistant, user_id, query)`.
* Queries are answered from inverted indexes (no catalogue scan) with cached typo matching: about 0.5 ms per query,
  roughly 2,000 queries/s per process on 5,000 products (`python bench.py`; was about 290/s before optimisation).
* Python threads share one GIL, so more threads do not add throughput. Scale with worker processes (for example
  `uvicorn app:app --workers N`, one index copy each) rather than threads.
* Build with `measure_tokens=False` (the default) when serving; the token estimates exist for `evaluate.py` reports.

## Caveats

* Known gap: "do you ship to Dubai?" returns the shipping section but not necessarily the sentence about shipping outside
  India, because nothing links place names to "outside". Real policy wording will need a few such synonyms.

* The bundled 100-product catalogue and 63 questions are synthetic and were written together with the assistant, so
  the perfect score on them is a regression check, not evidence of accuracy. Expect real misses on the 5,000-product catalogue
  and the 150 hidden questions, mostly around vocabulary (synonyms, spellings, Hinglish), ambiguity thresholds, and policy wording.
* The provided embedding and instruct models are not used yet. Natural extension points: embedding fallback in
  `CatalogueIndex.search` for queries the keyword vocabulary misses, and an LLM phrasing layer that may only rephrase
  already-cited sentences (keep the evaluator's checks as the gate).
* `evaluate.py` `load_cases` guesses the test-file field names; adjust once the real format is known.

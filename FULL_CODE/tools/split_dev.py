"""Split the pack's 40 dev questions into a tuning half and a check half, once, reproducibly.

Behaviour is tuned only on the "tune" half; the "check" half is scored at the end as an estimate on questions the
tuning never looked at. Conversations stay whole (a follow-up turn depends on its earlier turns), and each question
type is shuffled with a fixed seed and dealt alternately to the two halves, so both get a similar mix.

    python -m tools.split_dev        # writes data/dev_split.json
"""

from __future__ import annotations

import json
import random
from collections import defaultdict

from groundtruth.config import get_settings
from groundtruth.console import write_out
from groundtruth.evaluation.questions import conversations, load_questions, load_truth

SEED = 2026


def split(seed: int = SEED) -> dict:
    """Split the dev conversations into tune and check halves, balanced by question type; one seed, one split."""
    paths = get_settings().paths
    truth = load_truth(paths.dev_truth)
    by_type: dict[str, list[list[str]]] = defaultdict(list)
    for conv in conversations(load_questions(paths.dev_questions)):
        ids = [q.question_id for q in conv]
        by_type[truth[ids[0]]["question_type"]].append(ids)  # a conversation goes with its first turn's type
    rng = random.Random(seed)
    halves: dict[str, list[str]] = {"tune": [], "check": []}
    turn = 0
    for qtype in sorted(by_type):
        groups = sorted(by_type[qtype])
        rng.shuffle(groups)
        for ids in groups:  # alternate across types too, so odd-sized types don't all favour one half
            halves["tune" if turn % 2 == 0 else "check"].extend(ids)
            turn += 1
    return {"seed": seed, **{k: sorted(v) for k, v in halves.items()}}


def main() -> None:
    """Write the split to the dev-split file."""
    result = split()
    out = get_settings().paths.dev_split
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    write_out(f"wrote {out}: {len(result['tune'])} tune and {len(result['check'])} check questions")


if __name__ == "__main__":
    main()

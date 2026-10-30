"""Review the words a model proposed: `python main.py synonyms [list | approve WORD [TYPE] | reject WORD]`."""

from __future__ import annotations

import argparse

from .config import get_settings
from .console import write_out
from .synonyms import APPROVED, LearnedSynonyms


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The action, the word, and (for approve) an optional corrected product type."""
    ap.add_argument("action", nargs="?", default="list", choices=("list", "approve", "reject"))
    ap.add_argument("word", nargs="?", help="the shopper's word")
    ap.add_argument("target", nargs="?", help="approve only: use this product type instead of the proposed one")
    ap.add_argument("--file", default=str(get_settings().paths.synonyms), help="the synonym file")


def run(args: argparse.Namespace) -> None:
    """List proposals (most seen first), or approve or reject one. Approved words are used from the next question."""
    table = LearnedSynonyms(args.file)
    if args.action == "list":
        counts = table.counts()
        write_out(
            f"{counts['proposed']} waiting, {counts[APPROVED]} approved, {counts['rejected']} rejected ({args.file})"
        )
        for word, entry in table.pending():
            write_out(
                f"  {word:<20} -> {entry['target']:<14} seen {entry.get('seen', 0)}x  ({entry.get('source', '?')})"
            )
        return
    if not args.word:
        raise SystemExit(f"{args.action} needs a word")
    done = table.approve(args.word, args.target) if args.action == "approve" else table.reject(args.word)
    write_out(f"{args.action}d {args.word!r}" if done else f"{args.word!r} is not in {args.file}")

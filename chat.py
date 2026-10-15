"""Command-line chat. Usage: python chat.py [--catalogue PATH] [--policy PATH] [--meta]"""
from __future__ import annotations

import argparse
import sys

from shopassistant import Session, build_assistant

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", default="data/catalogue.jsonl")
    ap.add_argument("--policy", default="data/policy.txt")
    ap.add_argument("--meta", action="store_true", help="show action, latency and token estimates per reply")
    args = ap.parse_args()
    assistant = build_assistant(args.catalogue, args.policy, measure_tokens=args.meta)
    session = Session()
    print("Shopping assistant ready. Type a question (or 'quit'). Say 'start over' to clear context.")
    while True:
        try:
            query = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in {"quit", "exit", "q"}:
            break
        if not query:
            continue
        resp = assistant.ask(query, session)
        print(f"\nbot> {resp.text}")
        if args.meta:
            print(f"     [{resp.action} | {resp.latency_ms:.1f} ms | context~{resp.context_tokens} tok "
                  f"| output~{resp.output_tokens} tok]")


if __name__ == "__main__":
    main()

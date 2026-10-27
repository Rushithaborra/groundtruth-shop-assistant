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
    ap.add_argument("--embed-model", help="path to the provided embedding model (needs semantic_config.json)")
    ap.add_argument("--llm-model", help="path to the provided instruct model (last-resort query normaliser)")
    ap.add_argument("--meta", action="store_true", help="show action, latency and token estimates per reply")
    args = ap.parse_args()
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model,
                                measure_tokens=args.meta)
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
            llm = f" | llm {resp.llm_input_tokens}+{resp.llm_output_tokens} tok" if resp.path == "llm" else ""
            print(f"     [{resp.action} | path={resp.path} | {resp.latency_ms:.1f} ms | context~{resp.context_tokens} tok "
                  f"| output~{resp.output_tokens} tok{llm}]")


if __name__ == "__main__":
    main()

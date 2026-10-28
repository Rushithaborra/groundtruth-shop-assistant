"""Command-line chat. Usage: python main.py chat [--catalogue PATH] [--policy PATH] [--meta]"""
from __future__ import annotations

import argparse

from .answering import Session

from .builder import build_assistant
from .arguments import add_data_arguments, add_model_arguments
from .console import write_out


def add_arguments(ap: argparse.ArgumentParser) -> None:
    add_data_arguments(ap)
    add_model_arguments(ap)
    ap.add_argument("--meta", action="store_true", help="show action, latency and token estimates per reply")


def run(args: argparse.Namespace) -> None:
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model,
                                measure_tokens=args.meta)
    session = Session()
    write_out("Shopping assistant ready. Type a question (or 'quit'). Say 'start over' to clear context.")
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
        write_out(f"\nbot> {resp.text}")
        if args.meta:
            llm = f" | llm {resp.llm_input_tokens}+{resp.llm_output_tokens} tok" if resp.path == "llm" else ""
            write_out(f"     [{resp.action} | path={resp.path} | {resp.latency_ms:.1f} ms | context~{resp.context_tokens} "
                      f"tok | output~{resp.output_tokens} tok{llm}]")

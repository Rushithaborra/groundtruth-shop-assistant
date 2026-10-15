"""Throughput / latency benchmark, single-threaded and with concurrent users.

    python bench.py [--catalogue ...] [--questions ...] [--threads 8] [--rounds 20] [--profile]
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from shopassistant import Session, build_assistant


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", default="data/scale/catalogue.jsonl")
    ap.add_argument("--policy", default="data/scale/policy.txt")
    ap.add_argument("--questions", default="data/scale/hard_questions.jsonl")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--profile", action="store_true")
    args = ap.parse_args()

    start = time.perf_counter()
    assistant = build_assistant(args.catalogue, args.policy)
    print(f"build: {time.perf_counter() - start:.2f}s")
    convos = [json.loads(line)["turns"] for line in open(args.questions, encoding="utf-8") if line.strip()]

    def run(convo: list[str]) -> list[float]:
        session, times = Session(), []
        for turn in convo:
            t = time.perf_counter()
            assistant.ask(turn, session)
            times.append((time.perf_counter() - t) * 1000)
        return times

    def report(label: str, times: list[float], wall: float) -> None:
        q = statistics.quantiles(times, n=20)
        print(f"{label}: {len(times) / wall:8.0f} queries/s | mean {statistics.mean(times):6.2f} ms | "
              f"p50 {statistics.median(times):6.2f} | p95 {q[-1]:6.2f} | max {max(times):6.2f}")

    work = convos * args.rounds
    t0 = time.perf_counter()
    times = [x for c in work for x in run(c)]
    report("1 thread ", times, time.perf_counter() - t0)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(args.threads) as pool:
        times = [x for chunk in pool.map(run, work) for x in chunk]
    report(f"{args.threads} threads", times, time.perf_counter() - t0)

    if args.profile:
        prof = cProfile.Profile()
        prof.runcall(lambda: [run(c) for c in convos * 3])
        pstats.Stats(prof).sort_stats("cumulative").print_stats(14)


if __name__ == "__main__":
    main()

"""Throughput / latency benchmark, single-threaded and with concurrent users.

python main.py bench [--catalogue ...] [--questions ...] [--threads 8] [--rounds 20] [--profile]
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from groundtruth.answering import Session
from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.console import write_out


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The benchmark's options: data files (default: the 5,000-product demo), threads, rounds, profiling."""
    paths = get_settings().paths
    ap.add_argument("--catalogue", default=str(paths.demo_catalogue))
    ap.add_argument("--policy", default=str(paths.demo_policy))
    ap.add_argument("--questions", default=str(paths.bench_questions))
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--scale", type=int, default=0, metavar="N", help="generate and use an N-product catalogue instead")


SCALE_QUESTIONS = (
    "cotton kurta 1500 ke andar",
    "cheap running shoes under 2k",
    "black backpack for women",
    "sabse sasti bluetooth speaker",
    "blue cotton shirt size M",
    "runing shoes",
    "power bank 20000 mah",
    "wireless earbuds under 2000",
    "best rated kettle",
    "do you sell iphones?",
    "return policy",
)


def _scale_catalogue(n: int) -> str:
    """Write a generated catalogue of n products (messy, like the 5,000 demo one) to a temp file; returns its path."""
    import tempfile

    from tools import generate_scale

    generate_scale.N_PRODUCTS = n
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(p) for p in generate_scale.make_scale_catalogue()))
    return fh.name


def run(args: argparse.Namespace) -> None:
    """Time the assistant on its own, then with several threads at once, and optionally profile where time goes."""
    if args.scale:
        args.catalogue, args.questions = _scale_catalogue(args.scale), None
        args.policy = str(get_settings().paths.policy)  # the demo policy only exists after `generate`
    start = time.perf_counter()
    assistant = build_assistant(args.catalogue, args.policy)
    write_out(f"build: {time.perf_counter() - start:.2f}s for {len(assistant.index.products):,} products")
    if args.questions:
        with open(args.questions, encoding="utf-8") as fh:
            convos = [json.loads(line)["turns"] for line in fh if line.strip()]
    else:
        convos = [[q] for q in SCALE_QUESTIONS]

    def run_convo(convo: list[str]) -> list[float]:
        """Answer one conversation's turns in order in a fresh session; each turn's latency in milliseconds."""
        session, times = Session(), []
        for turn in convo:
            t = time.perf_counter()
            assistant.ask(turn, session)
            times.append((time.perf_counter() - t) * 1000)
        return times

    def report(label: str, times: list[float], wall: float) -> None:
        """Print queries per second and the mean, median, 95th-percentile and worst latency."""
        q = statistics.quantiles(times, n=20)
        write_out(
            f"{label}: {len(times) / wall:8.0f} queries/s | mean {statistics.mean(times):6.2f} ms | "
            f"p50 {statistics.median(times):6.2f} | p95 {q[-1]:6.2f} | max {max(times):6.2f}"
        )

    work = convos * args.rounds
    t0 = time.perf_counter()
    times = [x for c in work for x in run_convo(c)]
    report("1 thread ", times, time.perf_counter() - t0)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(args.threads) as pool:
        times = [x for chunk in pool.map(run_convo, work) for x in chunk]
    report(f"{args.threads} threads", times, time.perf_counter() - t0)

    if args.profile:
        prof = cProfile.Profile()
        prof.runcall(lambda: [run_convo(c) for c in convos * 3])
        pstats.Stats(prof).sort_stats("cumulative").print_stats(14)

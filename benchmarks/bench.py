"""nodina benchmark harness.

Runs every Phase 0 workload against both the native ``nodina`` agents and the
pure-Python reference schedulers, and emits a markdown table. The whole point is
to check whether the native machinery actually beats ~150 lines of plain Python.

Usage::

    uv run python benchmarks/bench.py            # print table
    uv run python benchmarks/bench.py --out FILE # also write markdown to FILE
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import statistics
import sys
import time
from dataclasses import dataclass

from nodnod.scope import Scope

import nodina
from benchmarks import reference, workloads


@dataclass
class Case:
    name: str
    mode: str  # "sync" | "async"
    node_factory: object
    runs: int
    check: object | None = None  # callable(scope) -> None, run once for correctness


def _median_ms(samples: list[float]) -> float:
    return statistics.median(samples) * 1000.0


def _time_sync(agent_cls, factory, runs: int, check) -> float | None:
    nodes = factory()
    agent = agent_cls.build(nodes)

    try:
        scope = Scope()
        agent.run(scope, {})
        if check is not None:
            check(scope)

        samples = []
        for _ in range(runs):
            scope = Scope()
            start = time.perf_counter()
            agent.run(scope, {})
            samples.append(time.perf_counter() - start)
    except Exception as exc:  # noqa: BLE001 - a broken agent is a real baseline result
        print(f"    !! {agent_cls.__name__} failed: {type(exc).__name__}: {exc}")
        return None
    return _median_ms(samples)


def _time_async(agent_cls, factory, runs: int, check, loop) -> float | None:
    nodes = factory()
    agent = agent_cls.build(nodes)

    try:
        scope = Scope()
        loop.run_until_complete(_as_coro(agent.run(scope, {})))
        if check is not None:
            check(scope)

        samples = []
        for _ in range(runs):
            scope = Scope()
            start = time.perf_counter()
            loop.run_until_complete(_as_coro(agent.run(scope, {})))
            samples.append(time.perf_counter() - start)
    except Exception as exc:  # noqa: BLE001 - a broken agent is a real baseline result
        print(f"    !! {agent_cls.__name__} failed: {type(exc).__name__}: {exc}")
        return None
    return _median_ms(samples)


async def _as_coro(awaitable):
    return await awaitable


CASES = [
    Case(
        "CPU-bound compose (200 nodes x 20k spins)",
        "sync",
        lambda: workloads.independent_cpu_nodes(200, 20_000),
        runs=5,
    ),
    Case(
        "CPU-bound compose (200 nodes x 20k spins)",
        "async",
        lambda: workloads.independent_cpu_nodes(200, 20_000),
        runs=5,
    ),
    Case(
        "I/O-bound compose (40 nodes x 10ms sleep)",
        "sync",
        lambda: workloads.independent_sleep_nodes(40, 0.010),
        runs=3,
    ),
    Case(
        "I/O-bound compose (40 nodes x 10ms asyncio.sleep)",
        "async",
        lambda: workloads.independent_async_sleep_nodes(40, 0.010),
        runs=3,
    ),
    Case("Wide DAG (500 independent trivial nodes)", "sync", lambda: workloads.wide_dag(500), runs=15),
    Case("Deep DAG (chain of 400)", "sync", lambda: workloads.deep_chain(400), runs=15),
    Case("Scope churn (tiny 2-node graph)", "sync", workloads.tiny_graph, runs=2000),
    Case("SequentialEither (5 failing fallbacks)", "sync", lambda: workloads.sequential_either(5), runs=300),
    Case("SequentialEither (5 failing fallbacks)", "async", lambda: workloads.sequential_either(5), runs=300),
    Case("ResultNode path", "sync", workloads.result_node_graph, runs=500),
    Case("ResultNode path", "async", workloads.result_node_graph, runs=500),
]


def run() -> list[tuple]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # A broken agent abandons asyncio tasks; silence the "never retrieved" noise.
    loop.set_exception_handler(lambda _loop, _ctx: None)
    rows = []
    try:
        for case in CASES:
            if case.mode == "sync":
                native = _time_sync(nodina.NodinaAgent, case.node_factory, case.runs, case.check)
                ref = _time_sync(reference.ReferenceSyncAgent, case.node_factory, case.runs, case.check)
            else:
                native = _time_async(nodina.AsyncNodinaAgent, case.node_factory, case.runs, case.check, loop)
                ref = _time_async(reference.ReferenceAsyncAgent, case.node_factory, case.runs, case.check, loop)
            rows.append((case.name, case.mode, native, ref))
            native_s = "BROKEN  " if native is None else f"{native:8.3f}ms"
            ref_s = "BROKEN  " if ref is None else f"{ref:8.3f}ms"
            print(f"  {case.mode:5s}  {case.name:50s}  nodina={native_s}  ref={ref_s}")
    finally:
        loop.close()
    return rows


def render(rows: list[tuple]) -> str:
    lines = [
        f"- Platform: {platform.platform()}",
        f"- Python: {sys.version.split()[0]}",
        f"- nodina backend: `{nodina.backend_name()}`",
        "",
        "| Case | Mode | nodina (ms) | pure-Python ref (ms) | nodina / ref |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for name, mode, native, ref in rows:
        native_s = "**BROKEN**" if native is None else f"{native:.3f}"
        ref_s = "**BROKEN**" if ref is None else f"{ref:.3f}"
        ratio_s = "n/a" if native is None or ref is None or not ref else f"{native / ref:.2f}x"
        lines.append(f"| {name} | {mode} | {native_s} | {ref_s} | {ratio_s} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    print("Running nodina benchmarks (median of N runs)...\n")
    rows = run()
    table = render(rows)
    print("\n" + table)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write("# nodina benchmark results\n\n")
            fh.write(table)
            fh.write("\n")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()

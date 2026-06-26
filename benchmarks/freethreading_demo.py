"""Demonstrate that nodina's native pool parallelizes CPU-bound nodes when the
GIL is off.

Run under a normal build and a free-threaded build (python3.14t) and compare:

    python -m benchmarks.freethreading_demo

On a GIL build, N independent CPU nodes take ~N x a single one (serialized).
On a free-threaded build they overlap and approach ~1x (up to core count).
"""

from __future__ import annotations

import sys
import time

from benchmarks import workloads
from nodina import NodinaAgent, Scope, backend_name

ITERS = 300_000  # ~tens of ms of pure-Python CPU churn per node
N = 8


def _time_once(agent) -> float:
    scope = Scope()
    start = time.perf_counter()
    agent.run(scope, {})
    return time.perf_counter() - start


def _measure(n: int, iters: int) -> float:
    nodes = workloads.independent_cpu_nodes(n, iters)
    agent = NodinaAgent.build(nodes)
    agent.run(Scope(), {})  # warm
    return min(_time_once(agent) for _ in range(3)) * 1000.0


def main() -> None:
    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    one = _measure(1, ITERS)
    many = _measure(N, ITERS)

    print(f"python {sys.version.split()[0]}  | GIL enabled: {gil}  | backend: {backend_name()}")
    print(f"  1 CPU node : {one:7.1f} ms")
    print(f"  {N} CPU nodes: {many:7.1f} ms   (serial would be ~{one * N:.0f} ms, parallel ~{one:.0f} ms)")
    print(f"  speedup vs serial: {one * N / many:.2f}x  (ideal ~{N}x with the GIL off)")


if __name__ == "__main__":
    main()

"""Node factories for the nodina benchmark suite.

Every factory returns a *fresh* set of node classes (nodnod caches a lot of
state on the class object, so a benchmark that re-runs the same graph must use
fresh nodes each time to measure real composition cost rather than scope-cache
hits).
"""

from __future__ import annotations

import asyncio
import itertools
import time

import kungfu
from nodnod.error import NodeError
from nodnod.interface.either import SequentialEither
from nodnod.interface.result_node import ResultNode
from nodnod.node import Node

_COUNTER = itertools.count()


def _uid() -> int:
    return next(_COUNTER)


def cpu_spin(iterations: int) -> int:
    """GIL-held integer churn -- a stand-in for real CPU work in a node."""
    acc = 0
    for _ in range(iterations):
        acc = (acc * 1103515245 + 12345) & 0x7FFFFFFF
    return acc


def _scalar(name: str, compose, annotations: dict) -> type[Node]:
    compose.__annotations__ = annotations
    return type(
        name,
        (Node,),
        {"__compose__": classmethod(compose), "__module__": __name__, "is_scalar": True},
    )


def _cpu_compose(i: int, iterations: int):
    def compose(cls):
        return cpu_spin(iterations) ^ i

    return compose


def _sleep_compose(i: int, seconds: float):
    def compose(cls):
        time.sleep(seconds)
        return i

    return compose


def _async_sleep_compose(i: int, seconds: float):
    async def compose(cls):
        await asyncio.sleep(seconds)
        return i

    return compose


def independent_cpu_nodes(count: int, iterations: int) -> set[type[Node]]:
    """``count`` independent nodes, each doing ``iterations`` of CPU churn."""
    uid = _uid()
    return {_scalar(f"Cpu{uid}_{i}", _cpu_compose(i, iterations), {"return": int}) for i in range(count)}


def independent_sleep_nodes(count: int, seconds: float) -> set[type[Node]]:
    """``count`` independent nodes, each blocking on ``time.sleep`` (releases GIL)."""
    uid = _uid()
    return {_scalar(f"Sleep{uid}_{i}", _sleep_compose(i, seconds), {"return": int}) for i in range(count)}


def independent_async_sleep_nodes(count: int, seconds: float) -> set[type[Node]]:
    """``count`` independent async nodes, each awaiting ``asyncio.sleep``."""
    uid = _uid()
    return {_scalar(f"ASleep{uid}_{i}", _async_sleep_compose(i, seconds), {"return": int}) for i in range(count)}


def wide_dag(count: int) -> set[type[Node]]:
    """``count`` independent trivial nodes -- pure scheduler-overhead probe."""
    return independent_cpu_nodes(count, 0)


def deep_chain(depth: int, iterations: int = 0) -> set[type[Node]]:
    """A single dependency chain of length ``depth``; returns the tip in a set."""
    uid = _uid()
    prev: type[Node] | None = None
    tip: type[Node] | None = None
    for i in range(depth):
        if prev is None:

            def compose(cls):
                return cpu_spin(iterations)

            node = _scalar(f"Deep{uid}_{i}", compose, {"return": int})
        else:

            def compose(cls, dep):
                return dep + 1

            node = _scalar(f"Deep{uid}_{i}", compose, {"dep": prev, "return": int})
        prev = node
        tip = node
    assert tip is not None
    return {tip}


def tiny_graph() -> set[type[Node]]:
    """A two-node chain used to measure per-``run()`` setup cost (scope churn)."""
    uid = _uid()

    def compose_a(cls):
        return 10

    a = _scalar(f"Churn{uid}_A", compose_a, {"return": int})

    def compose_b(cls, a):
        return a + 5

    b = _scalar(f"Churn{uid}_B", compose_b, {"a": a, "return": int})
    return {b}


def _bad_compose(i: int):
    def compose(cls):
        raise NodeError(f"bad option {i}")

    return compose


def sequential_either(fallback_depth: int) -> set[type[Node]]:
    """A SequentialEither whose first ``fallback_depth`` candidates fail."""
    uid = _uid()
    candidates: list[type[Node]] = []
    for i in range(fallback_depth):
        candidates.append(_scalar(f"Bad{uid}_{i}", _bad_compose(i), {"return": int}))

    def compose_good(cls):
        return 42

    candidates.append(_scalar(f"Good{uid}", compose_good, {"return": int}))

    choice = type(
        f"Choice{uid}",
        (SequentialEither,),
        {"__either__": tuple(candidates), "__type__": int, "is_scalar": True, "__module__": __name__},
    )
    return {choice}


def result_node_graph() -> set[type[Node]]:
    """A ResultNode wrapping a successful source node."""
    uid = _uid()

    def compose(cls):
        return 9

    source = _scalar(f"Source{uid}", compose, {"return": int})

    result = type(
        f"SourceResult{uid}",
        (ResultNode,),
        {
            "__from_node__": source,
            "__error__": ValueError,
            "__type__": kungfu.Result[int, ValueError],
            "__module__": __name__,
        },
    )
    return {result}

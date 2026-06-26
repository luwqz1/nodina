import asyncio
import sys
import time

import kungfu
import pytest


def _gil_enabled() -> bool:
    return sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True

from nodina import (
    AsyncNodinaAgent,
    ConcurrentEither,
    NodeError,
    NodinaAgent,
    ResultNode,
    Scope,
    SequentialEither,
    backend_name,
    scalar_node,
)


def _cpu_spin(iterations: int) -> int:
    acc = 0
    for _ in range(iterations):
        acc = (acc * 1103515245 + 12345) & 0x7FFFFFFF
    return acc


def test_backend_name_is_available():
    assert backend_name() == "cython"


def test_nodina_agent_runs_sync_dependencies():
    @scalar_node
    class A:
        @classmethod
        def __compose__(cls) -> int:
            return 10

    @scalar_node
    class B:
        @classmethod
        def __compose__(cls, a: A) -> int:
            return a + 5

    scope = Scope()
    NodinaAgent.build({B}).run(scope, {})

    assert scope[B].value == 15


def test_nodina_agent_overlaps_blocking_sync_nodes():
    # NodinaAgent offloads independent nodes onto a ThreadPoolExecutor, so
    # *blocking* (GIL-releasing) work like time.sleep genuinely overlaps. Two
    # 0.12s sleeps finish in well under their 0.24s sum. This is I/O concurrency
    # only -- it does NOT imply CPU parallelism (see the test below).
    @scalar_node
    class A:
        @classmethod
        def __compose__(cls) -> int:
            time.sleep(0.12)
            return 10

    @scalar_node
    class B:
        @classmethod
        def __compose__(cls) -> int:
            time.sleep(0.12)
            return 20

    scope = Scope()
    started = time.perf_counter()
    NodinaAgent.build({A, B}).run(scope, {})
    elapsed = time.perf_counter() - started

    assert scope[A].value == 10
    assert scope[B].value == 20
    assert elapsed < 0.22


def test_nodina_agent_cpu_parallelism_follows_gil():
    # The native pool dispatches independent nodes to threads. On a normal
    # (GIL) build, CPU-bound nodes still serialize on the GIL, so two of them
    # cost ~2x one. On a free-threaded build (python3.14t) they run truly in
    # parallel, so two cost ~1x one. This pins the real behavior either way.
    spins = 4_000_000

    @scalar_node
    class Solo:
        @classmethod
        def __compose__(cls) -> int:
            return _cpu_spin(spins)

    NodinaAgent.build({Solo}).run(Scope(), {})  # warm
    one_started = time.perf_counter()
    NodinaAgent.build({Solo}).run(Scope(), {})
    one = time.perf_counter() - one_started

    @scalar_node
    class A:
        @classmethod
        def __compose__(cls) -> int:
            return _cpu_spin(spins)

    @scalar_node
    class B:
        @classmethod
        def __compose__(cls) -> int:
            return _cpu_spin(spins)

    agent = NodinaAgent.build({A, B})
    agent.run(Scope(), {})  # warm
    two_started = time.perf_counter()
    agent.run(Scope(), {})
    two = time.perf_counter() - two_started

    if _gil_enabled():
        # GIL serializes CPU work: two independent nodes cost clearly more.
        assert two > 1.5 * one
    else:
        # Free-threaded: the two nodes overlap and stay close to a single one.
        assert two < 1.5 * one


def test_nodina_agent_rejects_async_nodes():
    @scalar_node
    class AsyncNode:
        @classmethod
        async def __compose__(cls) -> int:
            return 1

    scope = Scope()
    with pytest.raises(TypeError, match=r"use `nodina\.AsyncNodinaAgent`"):
        NodinaAgent.build({AsyncNode}).run(scope, {})


@pytest.mark.asyncio
async def test_async_nodina_agent_runs_async_node_with_asyncio_sleep():
    @scalar_node
    class AsyncNode:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0)
            return 8

    scope = Scope()
    await AsyncNodinaAgent.build({AsyncNode}).run(scope, {})

    assert scope[AsyncNode].value == 8


@pytest.mark.asyncio
async def test_async_nodina_agent_drives_real_asyncio_future():
    # Regression: a node that awaits a *real* future (asyncio.sleep > 0, not the
    # sleep(0) no-op) used to raise "await wasn't used with future" because the
    # compose driver double-stepped the future.
    @scalar_node
    class Slow:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0.01)
            return 7

    scope = Scope()
    await AsyncNodinaAgent.build({Slow}).run(scope, {})

    assert scope[Slow].value == 7


@pytest.mark.asyncio
async def test_async_nodina_agent_runs_independent_async_nodes_concurrently():
    # Real asyncio concurrency: two independent nodes that each await 0.1s must
    # overlap and finish in well under 0.2s.
    @scalar_node
    class A:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0.1)
            return 1

    @scalar_node
    class B:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0.1)
            return 2

    scope = Scope()
    started = time.perf_counter()
    await AsyncNodinaAgent.build({A, B}).run(scope, {})
    elapsed = time.perf_counter() - started

    assert scope[A].value == 1
    assert scope[B].value == 2
    assert elapsed < 0.18


@pytest.mark.asyncio
async def test_async_nodina_agent_runs_async_generator():
    closed = False

    @scalar_node
    class Resource:
        @classmethod
        async def __compose__(cls):
            nonlocal closed
            try:
                yield 11
            finally:
                closed = True

    scope = Scope()
    await AsyncNodinaAgent.build({Resource}).run(scope, {})
    assert scope[Resource].value == 11

    await scope.close()
    assert closed


@pytest.mark.asyncio
async def test_async_nodina_agent_runs_sequential_either_fallback():
    @scalar_node
    class Bad:
        @classmethod
        def __compose__(cls) -> int:
            raise NodeError("bad")

    @scalar_node
    class Good:
        @classmethod
        def __compose__(cls) -> int:
            return 42

    class Choice(SequentialEither):
        __either__ = (Bad, Good)
        __type__ = int
        is_scalar = True

    scope = Scope()
    await AsyncNodinaAgent.build({Choice}).run(scope, {})

    assert scope[Choice].value == 42


@pytest.mark.asyncio
async def test_async_nodina_agent_runs_result_node():
    @scalar_node
    class Source:
        @classmethod
        def __compose__(cls) -> int:
            return 9

    class SourceResult(ResultNode):
        __from_node__ = Source
        __error__ = ValueError
        __type__ = kungfu.Result[int, ValueError]

    scope = Scope()
    await AsyncNodinaAgent.build({SourceResult}).run(scope, {})

    assert kungfu.is_ok(scope[SourceResult].value)
    assert scope[SourceResult].value.value == 9


# --- either semantics (honesty: what is_concurrent actually does) -----------
#
# nodina selects the first *successful* either candidate in DECLARED order. It
# does not race by completion time and does not cancel losing candidates. The
# only difference between the two either kinds is composition strategy, driven
# by the dependency set nodnod derives from `is_concurrent`:
#   * SequentialEither composes candidates LAZILY (stops at the first success).
#   * ConcurrentEither composes ALL candidates concurrently, then selects.


def test_sequential_either_composes_candidates_lazily():
    composed: list[str] = []

    @scalar_node
    class First:
        @classmethod
        def __compose__(cls) -> int:
            composed.append("First")
            return 1

    @scalar_node
    class Second:
        @classmethod
        def __compose__(cls) -> int:
            composed.append("Second")
            return 2

    class Choice(SequentialEither):
        __either__ = (First, Second)
        __type__ = int
        is_scalar = True

    scope = Scope()
    NodinaAgent.build({Choice}).run(scope, {})

    assert scope[Choice].value == 1
    # First succeeds, so Second is never composed.
    assert composed == ["First"]


def test_concurrent_either_composes_all_candidates_and_overlaps():
    composed: list[str] = []

    @scalar_node
    class First:
        @classmethod
        def __compose__(cls) -> int:
            time.sleep(0.1)
            composed.append("First")
            return 1

    @scalar_node
    class Second:
        @classmethod
        def __compose__(cls) -> int:
            time.sleep(0.1)
            composed.append("Second")
            return 2

    class Choice(ConcurrentEither):
        __either__ = (First, Second)
        __type__ = int
        is_scalar = True

    scope = Scope()
    started = time.perf_counter()
    NodinaAgent.build({Choice}).run(scope, {})
    elapsed = time.perf_counter() - started

    # is_concurrent has a real, observable effect: BOTH candidates are composed
    # (unlike the lazy sequential case) and they overlap on the threadpool.
    assert scope[Choice].value == 1
    assert sorted(composed) == ["First", "Second"]
    assert elapsed < 0.18


@pytest.mark.asyncio
async def test_either_selection_is_declared_order_not_first_to_complete():
    # Honesty: nodina does NOT implement racing-by-completion. Even though the
    # second candidate finishes far sooner, the first declared success wins.
    @scalar_node
    class SlowWinner:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0.05)
            return 1

    @scalar_node
    class FastLoser:
        @classmethod
        async def __compose__(cls) -> int:
            await asyncio.sleep(0.0)
            return 2

    class Choice(ConcurrentEither):
        __either__ = (SlowWinner, FastLoser)
        __type__ = int
        is_scalar = True

    scope = Scope()
    await AsyncNodinaAgent.build({Choice}).run(scope, {})

    assert scope[Choice].value == 1

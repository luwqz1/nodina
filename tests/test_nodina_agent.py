import asyncio
import time

import kungfu
import pytest

from nodina import (
    AsyncNodinaAgent,
    NodeError,
    NodinaAgent,
    ResultNode,
    Scope,
    SequentialEither,
    backend_name,
    sleep,
    scalar_node,
)


def test_backend_name_is_available():
    assert backend_name() == "libuv"


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


def test_nodina_agent_runs_independent_sync_nodes_concurrently():
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
async def test_async_nodina_agent_runs_independent_async_nodes_concurrently():
    @scalar_node
    class A:
        @classmethod
        async def __compose__(cls) -> int:
            await sleep(120)
            return 10

    @scalar_node
    class B:
        @classmethod
        async def __compose__(cls) -> int:
            await sleep(120)
            return 20

    scope = Scope()
    started = time.perf_counter()
    await AsyncNodinaAgent.build({A, B}).run(scope, {})
    elapsed = time.perf_counter() - started

    assert scope[A].value == 10
    assert scope[B].value == 20
    assert elapsed < 0.22


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

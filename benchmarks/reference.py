"""Trivial pure-Python reference schedulers for nodnod nodes.

These exist purely as a *bar* for the native ``nodina`` backend: if the C/Cython
machinery cannot beat a few dozen lines of plain Python on a realistic workload,
the native code is not earning its place.

Two schedulers are provided, mirroring nodina's two agents:

* :class:`ReferenceSyncAgent` -- topological levels driven by a shared
  ``ThreadPoolExecutor`` (the only thing that can parallelise GIL-releasing
  nodes in a sync world).
* :class:`ReferenceAsyncAgent` -- one ``asyncio`` task per node, each awaiting
  its dependencies.

Both reuse only nodnod's *node-model* primitives (``Value``, ``__initialize__``,
generator helpers). The scheduling itself is hand-rolled here.
"""

from __future__ import annotations

import asyncio
import inspect
import types
from concurrent.futures import ThreadPoolExecutor

import kungfu
from nodnod.error import NodeError
from nodnod.interface.either import Either
from nodnod.interface.result_node import ResultNode
from nodnod.scope import validate_local_scope_is_linked_to_node_scopes
from nodnod.utils.generator import generator_asend, generator_send
from nodnod.value import Value

_POOL: ThreadPoolExecutor | None = None


def shared_pool() -> ThreadPoolExecutor:
    """Lazily created, process-wide thread pool (matches nodina's intended design)."""
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(thread_name_prefix="nodina-ref")
    return _POOL


def _traverse(roots) -> list:
    queue: list = []
    seen: set = set()

    def visit(node) -> None:
        if node in seen:
            return
        seen.add(node)
        for dep in getattr(node, "__dependencies__", ()):
            visit(dep)
        queue.append(node)

    for root in roots:
        visit(root)
    return queue


def _plain_dependencies(node, local_scope) -> set:
    deps: set = set()
    for dependency in node.__dependencies__:
        dep = local_scope.retrieve(dependency)
        if dep:
            deps.add(dep.unwrap())
    for injected in node.__injections__:
        deps.add(
            local_scope.retrieve(injected).expect(
                NodeError(f"couldn't inject `{injected.__name__}` because it was not set"),
            ),
        )
    return deps


def _deps_ready(node, results) -> bool:
    return all(dep in results for dep in getattr(node, "__dependencies__", ()))


def _dependency_failure(node, results):
    errors = [
        results[dep].error
        for dep in getattr(node, "__dependencies__", ())
        if dep in results and kungfu.is_err(results[dep])
    ]
    if errors and not hasattr(node, "__either__") and not issubclass(node, ResultNode):
        return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`", from_many=errors))
    return None


def _wrap_sync(cls, value) -> Value:
    if inspect.isawaitable(value):
        if hasattr(value, "close"):
            value.close()
        raise TypeError(f"`{cls.__name__}` returned an awaitable; use the async agent")
    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)
    return Value(cls, value)


async def _wrap_async(cls, value) -> Value:
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)
    if isinstance(value, types.AsyncGeneratorType):
        generated = (await generator_asend(value)).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)
    return Value(cls, value)


def _compose_result(node, node_scope, from_result):
    if kungfu.is_err(from_result):
        if not node.__compose__(from_result.error):
            raise from_result.error
        value = Value(node.__type__, from_result)
    else:
        value = Value(node.__type__, kungfu.Ok(from_result.value.value))
    node_scope[node] = value
    return kungfu.Ok(value)


class ReferenceSyncAgent:
    """Topological-level scheduler driven by a shared thread pool."""

    def __init__(self, traversed_nodes, final_nodes=None) -> None:
        self.traversed_nodes = list(traversed_nodes)
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    @classmethod
    def build(cls, nodes):
        return cls(_traverse(nodes), final_nodes=nodes)

    def run(self, local_scope, mapped_scopes) -> None:
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)
        results: dict = {}
        self._run(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    def _compose_plain(self, node, node_scope, local_scope, winner=None):
        cached = node_scope.retrieve(node)
        if cached:
            return kungfu.Ok(cached.unwrap())
        try:
            deps = {winner} if winner is not None else _plain_dependencies(node, local_scope)
            value = node.__initialize__(deps)
            node_scope[node] = _wrap_sync(node.__type__, value)
        except NodeError as e:
            return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))
        return kungfu.Ok(node_scope[node])

    def _compose_either(self, node, node_scope, local_scope, mapped_scopes, results):
        errors = []
        for candidate in node.__either__:
            result = results.get(candidate)
            if result is None:
                self._run(_traverse({candidate}), local_scope, mapped_scopes, results)
                result = results.get(candidate)
            if result is not None and result:
                scope = mapped_scopes.get(candidate, local_scope)
                scope[candidate] = result.unwrap()
                return self._compose_plain(node, node_scope, local_scope, result.unwrap())
            if result is not None:
                errors.append(result.error)
        return kungfu.Error(NodeError("no option found for either", from_many=errors))

    def _run(self, nodes, local_scope, mapped_scopes, results) -> None:
        pool = shared_pool()
        pending = [node for node in nodes if node not in results]
        pending_set = set(pending)

        while pending_set:
            ready = [node for node in pending if node in pending_set and _deps_ready(node, results)]
            if not ready:
                node = next(iter(pending_set))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")

            futures = {}
            for node in ready:
                pending_set.discard(node)
                failure = _dependency_failure(node, results)
                if failure is not None:
                    results[node] = failure
                    if kungfu.is_err(failure) and node in self.final_nodes:
                        raise failure.error
                    continue

                node_scope = mapped_scopes.get(node, local_scope)
                if issubclass(node, ResultNode):
                    results[node] = _compose_result(node, node_scope, results.get(node.__from_node__))
                elif issubclass(node, Either):
                    results[node] = self._compose_either(node, node_scope, local_scope, mapped_scopes, results)
                else:
                    futures[node] = pool.submit(self._compose_plain, node, node_scope, local_scope)

            for node, future in futures.items():
                results[node] = future.result()

            for node in ready:
                result = results.get(node)
                if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                    raise result.error

            pending = [node for node in pending if node in pending_set]


class ReferenceAsyncAgent:
    """One asyncio task per node; each task awaits its dependency tasks."""

    def __init__(self, traversed_nodes, final_nodes=None) -> None:
        self.traversed_nodes = list(traversed_nodes)
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    @classmethod
    def build(cls, nodes):
        return cls(_traverse(nodes), final_nodes=nodes)

    def run(self, local_scope, mapped_scopes):
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)
        return self._run_now(local_scope, mapped_scopes)

    async def _run_now(self, local_scope, mapped_scopes) -> None:
        results: dict = {}
        await self._run(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    async def _compose_plain(self, node, node_scope, local_scope, winner=None):
        cached = node_scope.retrieve(node)
        if cached:
            return kungfu.Ok(cached.unwrap())
        try:
            deps = {winner} if winner is not None else _plain_dependencies(node, local_scope)
            value = node.__initialize__(deps)
            node_scope[node] = await _wrap_async(node.__type__, value)
        except NodeError as e:
            return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))
        return kungfu.Ok(node_scope[node])

    async def _compose_either(self, node, node_scope, local_scope, mapped_scopes, results):
        errors = []
        for candidate in node.__either__:
            result = results.get(candidate)
            if result is None:
                await self._run(_traverse({candidate}), local_scope, mapped_scopes, results)
                result = results.get(candidate)
            if result is not None and result:
                scope = mapped_scopes.get(candidate, local_scope)
                scope[candidate] = result.unwrap()
                return await self._compose_plain(node, node_scope, local_scope, result.unwrap())
            if result is not None:
                errors.append(result.error)
        return kungfu.Error(NodeError("no option found for either", from_many=errors))

    async def _compose_node(self, node, local_scope, mapped_scopes, results):
        node_scope = mapped_scopes.get(node, local_scope)
        if issubclass(node, ResultNode):
            return _compose_result(node, node_scope, results.get(node.__from_node__))
        if issubclass(node, Either):
            return await self._compose_either(node, node_scope, local_scope, mapped_scopes, results)
        return await self._compose_plain(node, node_scope, local_scope)

    async def _run(self, nodes, local_scope, mapped_scopes, results) -> None:
        pending = [node for node in nodes if node not in results]
        pending_set = set(pending)

        while pending_set:
            ready = [node for node in pending if node in pending_set and _deps_ready(node, results)]
            if not ready:
                node = next(iter(pending_set))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")

            tasks = {}
            for node in ready:
                pending_set.discard(node)
                failure = _dependency_failure(node, results)
                if failure is not None:
                    results[node] = failure
                    if kungfu.is_err(failure) and node in self.final_nodes:
                        raise failure.error
                    continue
                tasks[node] = asyncio.ensure_future(self._compose_node(node, local_scope, mapped_scopes, results))

            if tasks:
                await asyncio.gather(*tasks.values())
                for node, task in tasks.items():
                    results[node] = task.result()

            for node in ready:
                result = results.get(node)
                if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                    raise result.error

            pending = [node for node in pending if node in pending_set]

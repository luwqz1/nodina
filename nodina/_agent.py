"""Pure-Python nodina agents.

History: this used to be a Cython extension that vendored libuv and offloaded
sync node composition onto libuv's threadpool. Benchmarks (see benchmarks/)
showed the libuv path was a dead heat with pure Python on CPU-bound work (the
work callback was ``with gil``, so nodes never ran in parallel), ~3.3x *slower*
than a plain ``ThreadPoolExecutor`` on blocking work, and broken for real
asyncio futures. So libuv — and Cython — were dropped.

* ``NodinaAgent`` (sync) resolves the DAG in dependency order, offloading
  independent nodes onto a shared, lazily created ``ThreadPoolExecutor`` so that
  blocking (GIL-releasing) nodes overlap.
* ``AsyncNodinaAgent`` resolves the DAG on the running asyncio event loop, one
  task per node.
"""

from __future__ import annotations

import asyncio
import inspect
import types
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import kungfu
from nodnod.agent import Agent
from nodnod.error import NodeError
from nodnod.interface.result_node import ResultNode
from nodnod.scope import validate_local_scope_is_linked_to_node_scopes
from nodnod.utils.generator import generator_asend, generator_send
from nodnod.value import Value

_BACKEND_NAME = "threadpool"

_POOL: ThreadPoolExecutor | None = None


def _shared_pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(thread_name_prefix="nodina")
    return _POOL


def backend_name() -> str:
    return _BACKEND_NAME


def _dedup(traversed_nodes) -> tuple:
    seen: dict = {}
    for node in traversed_nodes:
        if node not in seen:
            seen[node] = len(seen)
    return tuple(seen)


# --- traversal -------------------------------------------------------------


def _native_traverse(roots) -> tuple:
    queue: list = []
    seen: set = set()
    visiting: set = set()
    for root in roots:
        _native_visit(root, queue, seen, visiting)
    return tuple(queue)


def _native_visit(node, queue: list, seen: set, visiting: set) -> None:
    if node in seen:
        return
    if node in visiting:
        raise NodeError(f"circular dependency detected around `{node.__name__}`")
    visiting.add(node)
    for dep in getattr(node, "__dependencies__", ()):
        _native_visit(dep, queue, seen, visiting)
    visiting.remove(node)
    seen.add(node)
    queue.append(node)


# --- shared node-model helpers --------------------------------------------


def _node_dependencies(node, local_scope) -> set:
    dependencies: set = set()
    for dependency in node.__dependencies__:
        dep = local_scope.retrieve(dependency)
        if dep:
            dependencies.add(dep.unwrap())
    for injected_type in node.__injections__:
        dependencies.add(
            local_scope.retrieve(injected_type).expect(
                NodeError(f"couldn't inject `{injected_type.__name__}` because it was not set"),
            ),
        )
    return dependencies


def _either_dependencies(node, local_scope, winner) -> set:
    if winner is not None:
        return {winner}
    dependencies: set = set()
    for candidate in node.__either__:
        dep = local_scope.retrieve(candidate)
        if dep:
            dependencies.add(dep.unwrap())
            break
    return dependencies


def _compose_result_node(node, node_scope, from_result):
    if kungfu.is_err(from_result):
        if not node.__compose__(from_result.error):
            raise from_result.error
        value = Value(node.__type__, from_result)
    else:
        value = Value(node.__type__, kungfu.Ok(from_result.value.value))
    node_scope[node] = value
    return kungfu.Ok(value)


def _dependency_errors(node, results) -> list:
    return [
        results[dependency].error
        for dependency in getattr(node, "__dependencies__", ())
        if dependency in results and kungfu.is_err(results[dependency])
    ]


def _node_dependencies_resolved(node, results) -> bool:
    return all(dependency in results for dependency in getattr(node, "__dependencies__", ()))


def _dependency_failure_result(node, results):
    errors = _dependency_errors(node, results)
    if errors and not hasattr(node, "__either__") and not issubclass(node, ResultNode):
        return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`", from_many=errors))
    return None


# --- sync composition ------------------------------------------------------


def _sync_initialize_node(cls, value):
    if inspect.isawaitable(value):
        if hasattr(value, "close"):
            value.close()
        raise TypeError(f"`{cls.__name__}` returned an awaitable; use `nodina.AsyncNodinaAgent`")
    if isinstance(value, types.AsyncGeneratorType):
        raise TypeError(f"`{cls.__name__}` returned an async generator; use `nodina.AsyncNodinaAgent`")
    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)
    return Value(cls, value)


def _compose_sync_node(node, node_scope, local_scope, winner=None):
    cached = node_scope.retrieve(node)
    if cached:
        return kungfu.Ok(cached.unwrap())
    try:
        if hasattr(node, "__either__"):
            dependencies = _either_dependencies(node, local_scope, winner)
        else:
            dependencies = _node_dependencies(node, local_scope)
        value = node.__initialize__(dependencies)
        node_scope[node] = _sync_initialize_node(node.__type__, value)
    except NodeError as e:
        return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))
    return kungfu.Ok(node_scope[node])


def _compose_either_sync(agent, node, node_scope, local_scope, mapped_scopes, results):
    errors: list = []
    for dependency in node.__either__:
        result = results.get(dependency)
        if result is None:
            traverse = getattr(dependency, "__traverse__", None)
            if traverse is None:
                traverse = _native_traverse({dependency})
            agent._run_sync_nodes(traverse, local_scope, mapped_scopes, results)
            result = results.get(dependency)
        if result is not None and result:
            scope = mapped_scopes.get(dependency, local_scope)
            scope[dependency] = result.unwrap()
            return _compose_sync_node(node, node_scope, local_scope, result.unwrap())
        if result is not None:
            errors.append(result.error)
    return kungfu.Error(NodeError("no option found for either", from_many=errors))


def _compose_sync_node_from_results(agent, node, node_scope, local_scope, mapped_scopes, results):
    if issubclass(node, ResultNode):
        dep_result = results.get(node.__from_node__)
        if dep_result is None:
            return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`"))
        return _compose_result_node(node, node_scope, dep_result)
    if hasattr(node, "__either__"):
        return _compose_either_sync(agent, node, node_scope, local_scope, mapped_scopes, results)
    return _compose_sync_node(node, node_scope, local_scope)


# --- async composition -----------------------------------------------------


def _drive_awaitable(awaitable):
    iterator = awaitable.__await__()
    send_value = None
    while True:
        try:
            yielded = iterator.send(send_value)
            send_value = None
        except StopIteration as stop:
            return stop.value

        if yielded is None:
            send_value = yield None
            continue
        if asyncio.isfuture(yielded):
            send_value = yield from yielded.__await__()
            continue
        if inspect.isawaitable(yielded):
            send_value = yield from _drive_awaitable(yielded)
            continue
        send_value = yield yielded


def _async_initialize_node(cls, value):
    if inspect.isawaitable(value):
        value = yield from _drive_awaitable(value)
    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)
    if isinstance(value, types.AsyncGeneratorType):
        generated = (yield from _drive_awaitable(generator_asend(value))).expect(
            "Generator did not generate any value",
        )
        return Value(cls, generated, generator=value)
    return Value(cls, value)


def _compose_async_node(node, node_scope, local_scope, winner=None):
    cached = node_scope.retrieve(node)
    if cached:
        return kungfu.Ok(cached.unwrap())
    try:
        if hasattr(node, "__either__"):
            dependencies = _either_dependencies(node, local_scope, winner)
        else:
            dependencies = _node_dependencies(node, local_scope)
        value = node.__initialize__(dependencies)
        node_scope[node] = yield from _async_initialize_node(node.__type__, value)
    except NodeError as e:
        return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))
    return kungfu.Ok(node_scope[node])


def _compose_either_async(agent, node, node_scope, local_scope, mapped_scopes, results):
    errors: list = []
    for dependency in node.__either__:
        result = results.get(dependency)
        if result is None:
            traverse = getattr(dependency, "__traverse__", None)
            if traverse is None:
                traverse = _native_traverse({dependency})
            yield from agent._run_async_nodes(traverse, local_scope, mapped_scopes, results)
            result = results.get(dependency)
        if result is not None and result:
            scope = mapped_scopes.get(dependency, local_scope)
            scope[dependency] = result.unwrap()
            return (yield from _compose_async_node(node, node_scope, local_scope, result.unwrap()))
        if result is not None:
            errors.append(result.error)
    return kungfu.Error(NodeError("no option found for either", from_many=errors))


def _compose_async_node_from_results(agent, node, node_scope, local_scope, mapped_scopes, results):
    if issubclass(node, ResultNode):
        dep_result = results.get(node.__from_node__)
        if dep_result is None:
            return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`"))
        return _compose_result_node(node, node_scope, dep_result)
    if hasattr(node, "__either__"):
        return (yield from _compose_either_async(agent, node, node_scope, local_scope, mapped_scopes, results))
    return (yield from _compose_async_node(node, node_scope, local_scope))


class _GenAwaitable:
    """Adapts a nodina compose generator into an awaitable.

    asyncio's ``create_task`` only accepts native coroutines; a bare generator
    (even ``yield from``-based) is rejected, so we wrap it.
    """

    __slots__ = ("_gen",)

    def __init__(self, gen):
        self._gen = gen

    def __await__(self):
        return (yield from self._gen)


async def _as_coroutine(gen):
    return await _GenAwaitable(gen)


# --- agents ----------------------------------------------------------------


class AgentMixin:
    @classmethod
    def build(cls, nodes):
        return cls(_native_traverse(nodes), final_nodes=nodes)


class NodinaAgent(AgentMixin, Agent):
    def __init__(self, traversed_nodes, final_nodes=None):
        self.traversed_nodes = _dedup(traversed_nodes)
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    def run(self, local_scope, mapped_scopes):
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)
        results: dict = {}
        self._run_sync_nodes(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    def _run_sync_nodes(self, nodes, local_scope, mapped_scopes, results) -> None:
        pool = _shared_pool()
        pending = [node for node in nodes if node not in results]
        pending_set = set(pending)
        running: dict = {}  # future -> node

        while pending_set or running:
            progressed = False
            ready_plain: list = []

            for node in pending:
                if node not in pending_set or not _node_dependencies_resolved(node, results):
                    continue

                pending_set.discard(node)
                progressed = True

                failure = _dependency_failure_result(node, results)
                if failure is not None:
                    results[node] = failure
                    if kungfu.is_err(failure) and node in self.final_nodes:
                        raise failure.error
                    continue

                # Either/result resolution can recurse into _run_sync_nodes, so it
                # runs inline on this thread (never a pool worker) to avoid the
                # nested-loop re-entrancy the old libuv path suffered from.
                if issubclass(node, ResultNode) or hasattr(node, "__either__"):
                    node_scope = mapped_scopes.get(node, local_scope)
                    result = _compose_sync_node_from_results(
                        self, node, node_scope, local_scope, mapped_scopes, results,
                    )
                    self._record_sync(node, node_scope, result, results)
                else:
                    ready_plain.append(node)

            # Plain nodes go to the pool so blocking ones overlap -- but if a single
            # node is ready and nothing else is in flight (e.g. a dependency chain),
            # compose it inline to skip a pointless thread hand-off.
            if len(ready_plain) == 1 and not running:
                node = ready_plain[0]
                node_scope = mapped_scopes.get(node, local_scope)
                self._record_sync(node, node_scope, _compose_sync_node(node, node_scope, local_scope), results)
            else:
                for node in ready_plain:
                    running[pool.submit(_compose_sync_node, node, mapped_scopes.get(node, local_scope), local_scope)] = node

            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    node = running.pop(future)
                    self._record_sync(node, mapped_scopes.get(node, local_scope), future.result(), results)
                    progressed = True
            elif not progressed and pending_set:
                node = next(iter(pending_set))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")

            pending = [node for node in pending if node in pending_set]

    def _record_sync(self, node, node_scope, result, results) -> None:
        results[node] = result
        if result is not None and result:
            node_scope[node] = result.unwrap()
        if result is not None and kungfu.is_err(result) and node in self.final_nodes:
            raise result.error


class _AsyncRun:
    def __init__(self, agent, local_scope, mapped_scopes):
        self.agent = agent
        self.local_scope = local_scope
        self.mapped_scopes = mapped_scopes

    def __await__(self):
        yield from self.agent._run_now(self.local_scope, self.mapped_scopes)


class AsyncNodinaAgent(AgentMixin, Agent):
    def __init__(self, traversed_nodes, final_nodes=None):
        self.traversed_nodes = _dedup(traversed_nodes)
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    def run(self, local_scope, mapped_scopes, futures=None):
        if futures is not None:
            raise TypeError("`nodina.AsyncNodinaAgent` does not accept asyncio futures.")
        return _AsyncRun(self, local_scope, mapped_scopes)

    def _run_now(self, local_scope, mapped_scopes):
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)
        results: dict = {}
        yield from self._run_async_nodes(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    def _run_async_nodes(self, nodes, local_scope, mapped_scopes, results):
        pending = set(nodes)
        pending.difference_update(results.keys())
        running: dict = {}  # task -> (node, node_scope)

        while pending or running:
            progressed = False

            for node in tuple(pending):
                if not _node_dependencies_resolved(node, results):
                    continue

                pending.remove(node)
                progressed = True

                result = _dependency_failure_result(node, results)
                if result is not None:
                    results[node] = result
                    if kungfu.is_err(result) and node in self.final_nodes:
                        raise result.error
                    continue

                node_scope = mapped_scopes.get(node, local_scope)
                task = asyncio.create_task(
                    _as_coroutine(
                        _compose_async_node_from_results(
                            self, node, node_scope, local_scope, mapped_scopes, results,
                        ),
                    ),
                )
                running[task] = (node, node_scope)

            if running:
                done, _ = yield from asyncio.wait(
                    tuple(running.keys()),
                    return_when=asyncio.FIRST_COMPLETED,
                ).__await__()

                for task in done:
                    node, node_scope = running.pop(task)
                    result = task.result()

                    results[node] = result
                    if result is not None and result:
                        node_scope[node] = result.unwrap()

                    if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                        for pending_task in running:
                            pending_task.cancel()
                        raise result.error

                    progressed = True

            elif not progressed and pending:
                node = next(iter(pending))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")


__all__ = ("AsyncNodinaAgent", "NodinaAgent", "backend_name")

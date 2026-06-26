# cython: language_level=3str
# cython: boundscheck=False
# cython: wraparound=False
# cython: freethreading_compatible=True

"""Cython nodina agents backed by a native (libuv-free) pthread pool.

The DAG resolution is written once as a generator-based compose core; the sync
agent drives it to completion on a worker thread, the async agent awaits it on
the event loop. Independent sync nodes are dispatched to a shared pthread pool
(`nodina/core/nodina_pool.c`) whose queueing and waiting run WITHOUT the GIL --
the GIL is held only inside each `__compose__` call. On a normal CPython build
that overlaps blocking nodes (CPU nodes still serialize on the GIL); on a
free-threaded build the compose callbacks run truly in parallel.
"""

import asyncio
import inspect
import os
import threading
import types

import kungfu
from nodnod.agent import Agent
from nodnod.error import NodeError
from nodnod.interface.result_node import ResultNode
from nodnod.scope import validate_local_scope_is_linked_to_node_scopes
from nodnod.utils.generator import generator_asend, generator_send
from nodnod.value import Value

from cpython.ref cimport Py_INCREF, Py_DECREF, PyObject

from ._core cimport (
    nodina_pool,
    nodina_pool_completed,
    nodina_pool_new,
    nodina_pool_submit,
    nodina_pool_wait,
)

_BACKEND_NAME = "cython"

cdef nodina_pool *_POOL = NULL
_pool_lock = threading.Lock()
_tls = threading.local()


cdef int _default_threads() except -1:
    env = os.environ.get("NODINA_POOL_THREADS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    cpu = os.cpu_count() or 4
    return min(32, max(4, <int> cpu + 4))


cdef nodina_pool *_get_pool() except NULL:
    global _POOL
    if _POOL == NULL:
        # Double-checked locking: safe even if first use races across threads
        # on a free-threaded build.
        with _pool_lock:
            if _POOL == NULL:
                _POOL = nodina_pool_new(_default_threads())
                if _POOL == NULL:
                    raise MemoryError("could not create nodina threadpool")
    return _POOL


cdef bint _on_worker():
    return getattr(_tls, "on_worker", False)


def backend_name():
    return _BACKEND_NAME


def _dedup(traversed_nodes):
    seen = {}
    for node in traversed_nodes:
        if node not in seen:
            seen[node] = len(seen)
    return tuple(seen)


# --- traversal -------------------------------------------------------------


cdef tuple _native_traverse(object roots):
    cdef list queue = []
    cdef set seen = set()
    cdef set visiting = set()
    for root in roots:
        _native_visit(root, queue, seen, visiting)
    return tuple(queue)


cdef void _native_visit(object node, list queue, set seen, set visiting) except *:
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


# --- dependency resolution -------------------------------------------------


cdef set _node_dependencies(object node, object local_scope):
    cdef set dependencies = set()
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


cdef set _either_dependencies(object node, object local_scope, object winner):
    if winner is not None:
        return {winner}
    cdef set dependencies = set()
    for candidate in node.__either__:
        dep = local_scope.retrieve(candidate)
        if dep:
            dependencies.add(dep.unwrap())
            break
    return dependencies


cdef list _dependency_errors(object node, object results):
    return [
        results[dependency].error
        for dependency in getattr(node, "__dependencies__", ())
        if dependency in results and kungfu.is_err(results[dependency])
    ]


cdef bint _node_dependencies_resolved(object node, object results):
    for dependency in getattr(node, "__dependencies__", ()):
        if dependency not in results:
            return False
    return True


cdef object _dependency_failure_result(object node, object results):
    cdef list errors = _dependency_errors(node, results)
    if errors and not hasattr(node, "__either__") and not issubclass(node, ResultNode):
        return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`", from_many=errors))
    return None


# --- unified compose core (generators; sync mode never suspends) -----------


def _initialize_value(cls, value, async_mode):
    if inspect.isawaitable(value):
        if not async_mode:
            if hasattr(value, "close"):
                value.close()
            raise TypeError(f"`{cls.__name__}` returned an awaitable; use `nodina.AsyncNodinaAgent`")
        value = yield from value.__await__()

    if isinstance(value, types.AsyncGeneratorType):
        if not async_mode:
            raise TypeError(f"`{cls.__name__}` returned an async generator; use `nodina.AsyncNodinaAgent`")
        generated = (yield from generator_asend(value).__await__()).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)

    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)

    return Value(cls, value)


def _compose_node(node, node_scope, local_scope, async_mode, winner=None):
    cached = node_scope.retrieve(node)
    if cached:
        return kungfu.Ok(cached.unwrap())
    try:
        if hasattr(node, "__either__"):
            dependencies = _either_dependencies(node, local_scope, winner)
        else:
            dependencies = _node_dependencies(node, local_scope)
        value = node.__initialize__(dependencies)
        node_scope[node] = yield from _initialize_value(node.__type__, value, async_mode)
    except NodeError as e:
        return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))
    return kungfu.Ok(node_scope[node])


def _run_subtree(agent, traverse, local_scope, mapped_scopes, results, async_mode):
    if async_mode:
        yield from agent._run_async_nodes(traverse, local_scope, mapped_scopes, results)
    else:
        agent._run_sync_nodes(traverse, local_scope, mapped_scopes, results)


def _compose_either(agent, node, node_scope, local_scope, mapped_scopes, results, async_mode):
    """Select the first *successful* either candidate in declared order.

    Same for SequentialEither and ConcurrentEither: nodina does not race by
    completion time and does not cancel losing candidates. The kinds differ only
    in scheduling, via the dependency set nodnod derives from ``is_concurrent``
    (ConcurrentEither lists every candidate as a dependency -> composed
    concurrently; SequentialEither lists only the first -> composed lazily).
    """
    errors = []
    for dependency in node.__either__:
        result = results.get(dependency)
        if result is None:
            traverse = getattr(dependency, "__traverse__", None)
            if traverse is None:
                traverse = _native_traverse({dependency})
            yield from _run_subtree(agent, traverse, local_scope, mapped_scopes, results, async_mode)
            result = results.get(dependency)
        if result is not None and result:
            scope = mapped_scopes.get(dependency, local_scope)
            scope[dependency] = result.unwrap()
            return (yield from _compose_node(node, node_scope, local_scope, async_mode, result.unwrap()))
        if result is not None:
            errors.append(result.error)
    return kungfu.Error(NodeError("no option found for either", from_many=errors))


cdef object _compose_result_node(object node, object node_scope, object from_result):
    if kungfu.is_err(from_result):
        if not node.__compose__(from_result.error):
            raise from_result.error
        value = Value(node.__type__, from_result)
    else:
        value = Value(node.__type__, kungfu.Ok(from_result.value.value))
    node_scope[node] = value
    return kungfu.Ok(value)


def _compose_from_results(agent, node, node_scope, local_scope, mapped_scopes, results, async_mode):
    if issubclass(node, ResultNode):
        dep_result = results.get(node.__from_node__)
        if dep_result is None:
            return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`"))
        return _compose_result_node(node, node_scope, dep_result)
    if hasattr(node, "__either__"):
        return (yield from _compose_either(agent, node, node_scope, local_scope, mapped_scopes, results, async_mode))
    return (yield from _compose_node(node, node_scope, local_scope, async_mode))


# --- drivers ---------------------------------------------------------------


def _drive_sync(compose):
    try:
        compose.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("sync composition unexpectedly awaited")  # pragma: no cover


def _compose_sync(node, node_scope, local_scope):
    return _drive_sync(_compose_node(node, node_scope, local_scope, False))


class _GenAwaitable:
    """Adapts a compose generator into an awaitable for asyncio.create_task."""

    def __init__(self, gen):
        self._gen = gen

    def __await__(self):
        return (yield from self._gen)


async def _as_coroutine(gen):
    return await _GenAwaitable(gen)


# --- native pool task ------------------------------------------------------


cdef class _PoolTask:
    cdef object node
    cdef object node_scope
    cdef object local_scope
    cdef object result
    cdef object error
    cdef int done

    def __cinit__(self, object node, object node_scope, object local_scope):
        self.node = node
        self.node_scope = node_scope
        self.local_scope = local_scope
        self.result = None
        self.error = None
        self.done = 0


cdef void _pool_task_run(void *data) noexcept with gil:
    cdef _PoolTask task = <_PoolTask> data
    _tls.on_worker = True
    try:
        task.result = _compose_sync(task.node, task.node_scope, task.local_scope)
    except BaseException as exc:  # noqa: BLE001 - propagated to the scheduler thread
        task.error = exc
    task.done = 1
    Py_DECREF(task)  # release the reference taken at submit time


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
        results = {}
        self._run_sync_nodes(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    def _run_sync_nodes(self, nodes, local_scope, mapped_scopes, results):
        cdef nodina_pool *pool = _get_pool()
        cdef bint inline_only = _on_worker()
        cdef unsigned long long cursor
        cdef _PoolTask task
        cdef list pending = [node for node in nodes if node not in results]
        cdef set pending_set = set(pending)
        cdef list running = []
        cdef list ready_plain
        cdef list remaining
        cdef bint progressed
        cdef bint harvested

        while pending_set or running:
            progressed = False
            ready_plain = []

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
                # runs inline on this thread (never a pool worker).
                if issubclass(node, ResultNode) or hasattr(node, "__either__"):
                    node_scope = mapped_scopes.get(node, local_scope)
                    result = _compose_from_results(self, node, node_scope, local_scope, mapped_scopes, results, False)
                    result = _drive_sync(result)
                    self._record(node, node_scope, result, results)
                else:
                    ready_plain.append(node)

            if inline_only:
                # On a pool worker (nested run): never re-enter the pool.
                for node in ready_plain:
                    node_scope = mapped_scopes.get(node, local_scope)
                    self._record(node, node_scope, _compose_sync(node, node_scope, local_scope), results)
            elif len(ready_plain) == 1 and not running:
                # Lone ready node, nothing in flight (e.g. a chain): skip the pool.
                node = ready_plain[0]
                node_scope = mapped_scopes.get(node, local_scope)
                self._record(node, node_scope, _compose_sync(node, node_scope, local_scope), results)
            else:
                for node in ready_plain:
                    node_scope = mapped_scopes.get(node, local_scope)
                    task = _PoolTask(node, node_scope, local_scope)
                    running.append(task)
                    Py_INCREF(task)  # the pool borrows a reference until the task runs
                    if nodina_pool_submit(pool, _pool_task_run, <void *> <PyObject *> task) != 0:
                        Py_DECREF(task)
                        raise MemoryError("could not submit task to nodina pool")

            if running:
                # Wait (without the GIL) for >=1 task, then harvest every task
                # that is done in a single O(n) pass and hand control back to the
                # top so newly-unblocked nodes get dispatched.
                cursor = nodina_pool_completed(pool)
                while True:
                    remaining = []
                    harvested = False
                    for item in running:
                        task = <_PoolTask> item
                        if task.done:
                            harvested = True
                            if task.error is not None:
                                raise task.error
                            self._record(task.node, task.node_scope, task.result, results)
                            progressed = True
                        else:
                            remaining.append(item)
                    running = remaining
                    if harvested or not running:
                        break
                    with nogil:
                        nodina_pool_wait(pool, &cursor)

            elif not progressed and pending_set:
                node = next(iter(pending_set))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")

            pending = [node for node in pending if node in pending_set]

    def _record(self, node, node_scope, result, results):
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
        results = {}
        yield from self._run_async_nodes(self.traversed_nodes, local_scope, mapped_scopes, results)
        for node in self.final_nodes:
            result = results.get(node)
            if result is not None and kungfu.is_err(result):
                raise result.error

    def _run_async_nodes(self, nodes, local_scope, mapped_scopes, results):
        pending = set(nodes)
        pending.difference_update(results.keys())
        running = {}

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
                        _compose_from_results(self, node, node_scope, local_scope, mapped_scopes, results, True),
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

# cython: language_level=3str
# cython: boundscheck=False
# cython: wraparound=False
# cython: initializedcheck=False

from cpython.mem cimport PyMem_Free, PyMem_Malloc
from cpython.ref cimport PyObject
from libc.stddef cimport size_t
from libc.stdint cimport uint64_t

import inspect
import asyncio
import types

import kungfu

from nodnod.agent import Agent
from nodnod.error import NodeError
from nodnod.scope import validate_local_scope_is_linked_to_node_scopes
from nodnod.utils.generator import generator_asend, generator_send
from nodnod.value import Value

from ._core cimport (
    NODINA_NODE_CONCURRENT_EITHER,
    NODINA_NODE_EITHER,
    NODINA_NODE_RESULT,
    nodina_mutex,
    nodina_mutex_free,
    nodina_mutex_lock,
    nodina_mutex_new,
    nodina_mutex_unlock,
    nodina_plan,
    nodina_plan_free,
    nodina_plan_new,
    nodina_plan_set_node,
    nodina_run_state,
    nodina_run_state_free,
    nodina_run_state_mark_finished,
    nodina_run_state_mark_started,
    nodina_run_state_new,
    nodina_uv_available,
    nodina_uv_backend_name,
    nodina_uv_work,
    nodina_uv_work_free,
    nodina_uv_work_is_done,
    nodina_uv_work_status,
    nodina_uv_runner,
    nodina_uv_runner_free,
    nodina_uv_runner_new,
    nodina_uv_runner_queue_work,
    nodina_uv_runner_run_once,
    nodina_uv_runner_sleep,
)

cdef object _Either = None
cdef object _ResultNode = None


cdef object _either_type():
    global _Either

    if _Either is None:
        from nodnod.interface.either import Either

        _Either = Either

    return _Either


cdef object _result_node_type():
    global _ResultNode

    if _ResultNode is None:
        from nodnod.interface.result_node import ResultNode

        _ResultNode = ResultNode

    return _ResultNode


cdef inline void _lock(nodina_mutex *mutex) noexcept nogil:
    nodina_mutex_lock(mutex)


cdef inline void _unlock(nodina_mutex *mutex) noexcept nogil:
    nodina_mutex_unlock(mutex)


def libuv_available():
    cdef int available

    with nogil:
        available = nodina_uv_available()

    return available != 0


def backend_name():
    cdef const char *name

    with nogil:
        name = nodina_uv_backend_name()

    return name.decode("ascii")


cdef class _NativePlan:
    cdef nodina_plan *plan
    cdef dict index_by_node
    cdef tuple nodes
    cdef tuple final_nodes
    cdef nodina_mutex *mutex
    cdef bint mutex_ready

    def __cinit__(self):
        self.plan = NULL
        self.mutex = NULL
        self.mutex_ready = False

    def __init__(self, object traversed_nodes, object final_nodes):
        cdef Py_ssize_t i
        cdef int rc
        cdef list nodes_list = []
        cdef dict seen = {}
        cdef object node

        for node in traversed_nodes:
            if node in seen:
                continue

            seen[node] = len(nodes_list)
            nodes_list.append(node)

        self.nodes = tuple(nodes_list)
        self.index_by_node = seen
        self.final_nodes = tuple(final_nodes) if final_nodes is not None else self.nodes

        rc = nodina_mutex_new(&self.mutex)
        if rc != 0:
            raise MemoryError(f"could not initialize nodina mutex: {rc}")

        self.mutex_ready = True

        rc = nodina_plan_new(<size_t> len(self.nodes), &self.plan)
        if rc != 0:
            raise MemoryError(f"could not allocate nodina plan: {rc}")

        for i, node in enumerate(self.nodes):
            self._set_node(<size_t> i, node)

    cdef void _set_node(self, size_t index, object node):
        cdef list dep_indexes = []
        cdef object dep
        cdef size_t dep_count
        cdef size_t *raw_deps = NULL
        cdef Py_ssize_t i
        cdef int rc
        cdef unsigned int flags = 0

        if issubclass(node, _either_type()):
            flags |= NODINA_NODE_EITHER

            if getattr(node, "is_concurrent", False):
                flags |= NODINA_NODE_CONCURRENT_EITHER

        if issubclass(node, _result_node_type()):
            flags |= NODINA_NODE_RESULT

        for dep in getattr(node, "__dependencies__", ()):
            if dep in self.index_by_node:
                dep_indexes.append(self.index_by_node[dep])

        dep_count = <size_t> len(dep_indexes)
        if dep_count > 0:
            raw_deps = <size_t *> PyMem_Malloc(sizeof(size_t) * dep_count)

            if raw_deps == NULL:
                raise MemoryError("could not allocate dependency index buffer")

            try:
                for i, dep in enumerate(dep_indexes):
                    raw_deps[i] = <size_t> dep

                rc = nodina_plan_set_node(
                    self.plan,
                    index,
                    <void *> <PyObject *> node,
                    raw_deps,
                    dep_count,
                    flags,
                )
            finally:
                PyMem_Free(raw_deps)
        else:
            rc = nodina_plan_set_node(
                self.plan,
                index,
                <void *> <PyObject *> node,
                NULL,
                0,
                flags,
            )

        if rc != 0:
            raise MemoryError(f"could not set nodina plan node: {rc}")

    def __dealloc__(self):
        if self.plan != NULL:
            nodina_plan_free(self.plan)
            self.plan = NULL

        if self.mutex_ready:
            nodina_mutex_free(self.mutex)
            self.mutex = NULL
            self.mutex_ready = False

    @property
    def traversed_nodes(self):
        return self.nodes

    cdef void mark_started(self, nodina_run_state *state, size_t index) noexcept nogil:
        _lock(self.mutex)
        nodina_run_state_mark_started(state, index)
        _unlock(self.mutex)

    cdef void mark_finished(self, nodina_run_state *state, size_t index) noexcept nogil:
        _lock(self.mutex)
        nodina_run_state_mark_finished(state, index)
        _unlock(self.mutex)


cdef class _RunState:
    cdef nodina_run_state *state

    def __cinit__(self):
        self.state = NULL

    def __init__(self, Py_ssize_t length):
        cdef int rc
        rc = nodina_run_state_new(<size_t> length, &self.state)
        if rc != 0:
            raise MemoryError(f"could not allocate nodina run state: {rc}")

    def __dealloc__(self):
        if self.state != NULL:
            nodina_run_state_free(self.state)
            self.state = NULL


cdef class _UvRunner:
    cdef nodina_uv_runner *runner

    def __cinit__(self):
        self.runner = NULL

    def __init__(self):
        cdef int rc
        rc = nodina_uv_runner_new(&self.runner)
        if rc != 0:
            raise RuntimeError(f"could not initialize libuv runner: {rc}")

    def __dealloc__(self):
        if self.runner != NULL:
            nodina_uv_runner_free(self.runner)
            self.runner = NULL

    cpdef sleep(self, uint64_t timeout_ms):
        cdef int rc

        with nogil:
            rc = nodina_uv_runner_sleep(self.runner, timeout_ms)

        if rc != 0:
            raise RuntimeError(f"libuv sleep failed: {rc}")


cdef class _NodinaSleep:
    cdef uint64_t timeout_ms

    def __init__(self, object timeout_ms):
        if timeout_ms < 0:
            raise ValueError("timeout must be >= 0")
        self.timeout_ms = <uint64_t> timeout_ms

    def __await__(self):
        yield self
        return None


def sleep(object timeout_ms):
    return _NodinaSleep(timeout_ms)


cdef tuple _native_traverse(object roots):
    cdef list queue = []
    cdef set seen = set()
    cdef set visiting = set()
    cdef object root

    for root in roots:
        _native_visit(root, queue, seen, visiting)

    return tuple(queue)


cdef void _native_visit(object node, list queue, set seen, set visiting):
    cdef object dep

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


cdef set _node_dependencies(object node, object local_scope):
    cdef set dependencies = set()
    cdef object dependency
    cdef object dep
    cdef object injected_type

    for dependency in node.__dependencies__:
        dep = local_scope.retrieve(dependency)

        if dep:
            dependencies.add(dep.unwrap())

    for injected_type in node.__injections__:
        dependencies.add(
            local_scope
            .retrieve(injected_type)
            .expect(NodeError(f"couldn't inject `{injected_type.__name__}` because it was not set"))
        )

    return dependencies


cdef object _sync_initialize_node(object cls, object value):
    cdef object generated

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


cdef object _compose_sync_node(object node, object node_scope, object local_scope, object winner=None):
    cdef object cached
    cdef object dependencies
    cdef object candidate
    cdef object dep
    cdef object value

    cached = node_scope.retrieve(node)
    if cached:
        return kungfu.Ok(cached.unwrap())

    try:
        if hasattr(node, "__either__"):
            if winner is not None:
                dependencies = {winner}
            else:
                dependencies = set()
                for candidate in node.__either__:
                    dep = local_scope.retrieve(candidate)
                    if dep:
                        dependencies.add(dep.unwrap())
                        break
        else:
            dependencies = _node_dependencies(node, local_scope)

        value = node.__initialize__(dependencies)
        node_scope[node] = _sync_initialize_node(node.__type__, value)
    except NodeError as e:
        return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))

    return kungfu.Ok(node_scope[node])


cdef object _compose_result_node(object node, object node_scope, object from_result):
    cdef object value

    if kungfu.is_err(from_result):
        if not node.__compose__(from_result.error):
            raise from_result.error
        value = Value(node.__type__, from_result)
    else:
        value = Value(node.__type__, kungfu.Ok(from_result.value.value))

    node_scope[node] = value
    return kungfu.Ok(value)


cdef object _compose_either_sync(
    object agent,
    object node,
    object node_scope,
    object local_scope,
    object mapped_scopes,
    object results,
):
    cdef list errors = []
    cdef object dependency
    cdef object result
    cdef object scope
    cdef object traverse

    # Concurrent Either cannot race without a scheduler. In the no-asyncio
    # runtime it is resolved by first successful candidate in declared order.
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


cdef object _dependency_errors(object node, object results):
    cdef list errors = []
    cdef object dependency
    cdef object dep_result

    for dependency in getattr(node, "__dependencies__", ()):
        dep_result = results.get(dependency)

        if dep_result is not None and kungfu.is_err(dep_result):
            errors.append(dep_result.error)

    return errors


cdef bint _node_dependencies_resolved(object node, object results):
    cdef object dependency

    for dependency in getattr(node, "__dependencies__", ()):
        if dependency not in results:
            return False

    return True


cdef object _dependency_failure_result(object node, object results):
    cdef list errors = _dependency_errors(node, results)

    if errors and not hasattr(node, "__either__") and not issubclass(node, _result_node_type()):
        return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`", from_many=errors))

    return None


cdef object _compose_sync_node_from_results(
    object agent,
    object node,
    object node_scope,
    object local_scope,
    object mapped_scopes,
    object results,
):
    cdef object dep_result

    if issubclass(node, _result_node_type()):
        dep_result = results.get(node.__from_node__)

        if dep_result is None:
            return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`"))

        return _compose_result_node(node, node_scope, dep_result)

    if hasattr(node, "__either__"):
        return _compose_either_sync(agent, node, node_scope, local_scope, mapped_scopes, results)

    return _compose_sync_node(node, node_scope, local_scope)


cdef class _UvWorkTask:
    cdef nodina_uv_work *work
    cdef object agent
    cdef object node
    cdef object node_scope
    cdef object local_scope
    cdef object mapped_scopes
    cdef object results
    cdef object result
    cdef object error

    def __cinit__(self):
        self.work = NULL

    def __init__(
        self,
        object agent,
        object node,
        object node_scope,
        object local_scope,
        object mapped_scopes,
        object results,
    ):
        self.agent = agent
        self.node = node
        self.node_scope = node_scope
        self.local_scope = local_scope
        self.mapped_scopes = mapped_scopes
        self.results = results
        self.result = None
        self.error = None

    def __dealloc__(self):
        if self.work != NULL:
            nodina_uv_work_free(self.work)
            self.work = NULL

    cdef void start(self, _UvRunner runner):
        cdef int rc
        cdef nodina_uv_work *work = NULL

        rc = nodina_uv_runner_queue_work(
            runner.runner,
            _uv_work_task_run,
            _uv_work_task_done,
            <void *> <PyObject *> self,
            &work,
        )
        if rc != 0:
            raise RuntimeError(f"libuv work queue failed: {rc}")

        self.work = work


cdef void _uv_work_task_run(void *data) noexcept with gil:
    cdef _UvWorkTask task = <_UvWorkTask> data

    try:
        task.result = _compose_sync_node_from_results(
            task.agent,
            task.node,
            task.node_scope,
            task.local_scope,
            task.mapped_scopes,
            task.results,
        )
    except BaseException as exc:
        task.error = exc


cdef void _uv_work_task_done(void *data, int status) noexcept with gil:
    pass


def _drive_awaitable(object awaitable, _UvRunner runner):
    cdef object iterator
    cdef object yielded = None
    cdef object send_value = None

    iterator = awaitable.__await__()

    while True:
        try:
            yielded = iterator.send(send_value)
            send_value = None
        except StopIteration as stop:
            return stop.value

        if isinstance(yielded, _NodinaSleep):
            send_value = yield from asyncio.sleep((<_NodinaSleep> yielded).timeout_ms / 1000).__await__()
            continue

        if yielded is None:
            send_value = yield None
            continue

        if asyncio.isfuture(yielded):
            send_value = yield from yielded.__await__()
            continue

        if inspect.isawaitable(yielded):
            send_value = yield from _drive_awaitable(yielded, runner)
            continue

        send_value = yield yielded


def _async_initialize_node(object cls, object value, _UvRunner runner):
    cdef object generated

    if inspect.isawaitable(value):
        value = yield from _drive_awaitable(value, runner)

    if isinstance(value, types.GeneratorType):
        generated = generator_send(value).expect("Generator did not generate any value")
        return Value(cls, generated, generator=value)

    if isinstance(value, types.AsyncGeneratorType):
        generated = (yield from _drive_awaitable(generator_asend(value), runner)).expect(
            "Generator did not generate any value"
        )
        return Value(cls, generated, generator=value)

    return Value(cls, value)


def _compose_async_node(object node, object node_scope, object local_scope, _UvRunner runner, object winner=None):
    cdef object cached
    cdef object dependencies
    cdef object candidate
    cdef object dep
    cdef object value

    cached = node_scope.retrieve(node)
    if cached:
        return kungfu.Ok(cached.unwrap())

    try:
        if hasattr(node, "__either__"):
            if winner is not None:
                dependencies = {winner}
            else:
                dependencies = set()

                for candidate in node.__either__:
                    dep = local_scope.retrieve(candidate)

                    if dep:
                        dependencies.add(dep.unwrap())
                        break
        else:
            dependencies = _node_dependencies(node, local_scope)

        value = node.__initialize__(dependencies)
        node_scope[node] = yield from _async_initialize_node(node.__type__, value, runner)
    except NodeError as e:
        return kungfu.Error(NodeError(f"failed to compose `{node.__name__}`", from_error=e))

    return kungfu.Ok(node_scope[node])


def _compose_either_async(
    object agent,
    object node,
    object node_scope,
    object local_scope,
    object mapped_scopes,
    object results,
    _UvRunner runner,
):
    cdef list errors = []
    cdef object dependency
    cdef object result
    cdef object scope
    cdef object traverse

    for dependency in node.__either__:
        result = results.get(dependency)

        if result is None:
            traverse = getattr(dependency, "__traverse__", None)

            if traverse is None:
                traverse = _native_traverse({dependency})

            yield from agent._run_async_nodes(traverse, local_scope, mapped_scopes, results, runner)
            result = results.get(dependency)

        if result is not None and result:
            scope = mapped_scopes.get(dependency, local_scope)
            scope[dependency] = result.unwrap()
            return (yield from _compose_async_node(node, node_scope, local_scope, runner, result.unwrap()))

        if result is not None:
            errors.append(result.error)

    return kungfu.Error(NodeError("no option found for either", from_many=errors))


def _compose_async_node_from_results(
    object agent,
    object node,
    object node_scope,
    object local_scope,
    object mapped_scopes,
    object results,
    _UvRunner runner,
):
    cdef object dep_result

    if issubclass(node, _result_node_type()):
        dep_result = results.get(node.__from_node__)

        if dep_result is None:
            return kungfu.Error(NodeError(f"could not resolve dependencies of `{node.__name__}`"))

        return _compose_result_node(node, node_scope, dep_result)

    if hasattr(node, "__either__"):
        return (yield from _compose_either_async(agent, node, node_scope, local_scope, mapped_scopes, results, runner))

    return (yield from _compose_async_node(node, node_scope, local_scope, runner))


@types.coroutine
def _run_async_generator(object awaitable):
    return (yield from awaitable)


cdef class AgentMixin:
    @classmethod
    def build(cls, nodes):
        return cls(_native_traverse(nodes), final_nodes=nodes)


cdef class NodinaAgent(AgentMixin, Agent):
    cdef _NativePlan native
    cdef object traversed_nodes
    cdef object final_nodes
    cdef dict __dict__

    def __init__(self, object traversed_nodes, object final_nodes=None):
        self.native = _NativePlan(traversed_nodes, final_nodes if final_nodes is not None else traversed_nodes)
        self.traversed_nodes = self.native.traversed_nodes
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    def run(self, local_scope, mapped_scopes):
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)

        cdef dict results = {}
        self._run_sync_nodes(self.traversed_nodes, local_scope, mapped_scopes, results)

        cdef object node
        cdef object result

        for node in self.final_nodes:
            result = results.get(node)

            if result is not None and kungfu.is_err(result):
                raise result.error

    cpdef _run_sync_nodes(self, object nodes, object local_scope, object mapped_scopes, object results):
        cdef _RunState state = _RunState(len(nodes))
        cdef Py_ssize_t i
        cdef object node
        cdef object result
        cdef object node_scope
        cdef object pending = set(nodes)
        cdef dict index_by_node = {node: i for i, node in enumerate(nodes)}
        cdef list running = []
        cdef list completed = []
        cdef _UvRunner runner = _UvRunner()
        cdef _UvWorkTask task
        cdef int rc
        cdef bint progressed

        pending.difference_update(results.keys())

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
                    if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                        raise result.error
                    continue

                i = index_by_node[node]
                with nogil:
                    self.native.mark_started(state.state, <size_t> i)

                node_scope = mapped_scopes.get(node, local_scope)
                task = _UvWorkTask(self, node, node_scope, local_scope, mapped_scopes, results)
                task.start(runner)
                running.append(task)

            completed.clear()
            for task in running:
                with nogil:
                    rc = nodina_uv_work_is_done(task.work)

                if rc != 0:
                    completed.append(task)

            for task in completed:
                running.remove(task)
                node = task.node
                result = task.result

                with nogil:
                    rc = nodina_uv_work_status(task.work)

                if rc != 0:
                    raise RuntimeError(f"libuv work item failed: {rc}")

                if task.error is not None:
                    raise task.error

                results[node] = result
                if result is not None and result:
                    task.node_scope[node] = result.unwrap()

                i = index_by_node[node]
                with nogil:
                    self.native.mark_finished(state.state, <size_t> i)

                if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                    raise result.error

                progressed = True

            if running:
                with nogil:
                    rc = nodina_uv_runner_run_once(runner.runner)
                if rc < 0:
                    raise RuntimeError(f"libuv runner failed: {rc}")
            elif not progressed and pending:
                node = next(iter(pending))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")


cdef class _AsyncRun:
    cdef AsyncNodinaAgent agent
    cdef object local_scope
    cdef object mapped_scopes

    def __init__(self, AsyncNodinaAgent agent, object local_scope, object mapped_scopes):
        self.agent = agent
        self.local_scope = local_scope
        self.mapped_scopes = mapped_scopes

    def __await__(self):
        yield from self.agent._run_now(self.local_scope, self.mapped_scopes)
        return None


cdef class AsyncNodinaAgent(AgentMixin, Agent):
    cdef _NativePlan native
    cdef object traversed_nodes
    cdef object final_nodes
    cdef dict __dict__

    def __init__(self, object traversed_nodes, object final_nodes=None):
        self.native = _NativePlan(traversed_nodes, final_nodes if final_nodes is not None else traversed_nodes)
        self.traversed_nodes = self.native.traversed_nodes
        self.final_nodes = set(final_nodes) if final_nodes is not None else set(self.traversed_nodes)

    def run(self, local_scope, mapped_scopes, futures=None):
        if futures is not None:
            raise TypeError("`nodina.AsyncNodinaAgent` does not accept asyncio futures.")
        return _AsyncRun(self, local_scope, mapped_scopes)

    def _run_now(self, object local_scope, object mapped_scopes):
        validate_local_scope_is_linked_to_node_scopes(local_scope, mapped_scopes)

        cdef dict results = {}
        cdef _UvRunner runner = _UvRunner()
        yield from self._run_async_nodes(self.traversed_nodes, local_scope, mapped_scopes, results, runner)

        cdef object node
        cdef object result

        for node in self.final_nodes:
            result = results.get(node)

            if result is not None and kungfu.is_err(result):
                raise result.error

    def _run_async_nodes(
        self,
        object nodes,
        object local_scope,
        object mapped_scopes,
        object results,
        _UvRunner runner,
    ):
        cdef _RunState state = _RunState(len(nodes))
        cdef Py_ssize_t i
        cdef object node
        cdef object result
        cdef object node_scope
        cdef object pending = set(nodes)
        cdef dict index_by_node = {node: i for i, node in enumerate(nodes)}
        cdef dict running = {}
        cdef object done
        cdef object pending_tasks
        cdef object task
        cdef bint progressed

        pending.difference_update(results.keys())

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
                    if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                        raise result.error
                    continue

                i = index_by_node[node]
                with nogil:
                    self.native.mark_started(state.state, <size_t> i)

                node_scope = mapped_scopes.get(node, local_scope)
                task = asyncio.create_task(
                    _run_async_generator(
                        _compose_async_node_from_results(
                            self,
                            node,
                            node_scope,
                            local_scope,
                            mapped_scopes,
                            results,
                            runner,
                        )
                    )
                )
                running[task] = (node, node_scope)

            if running:
                done, pending_tasks = yield from asyncio.wait(
                    tuple(running.keys()),
                    return_when=asyncio.FIRST_COMPLETED,
                ).__await__()

                for task in done:
                    node, node_scope = running.pop(task)
                    result = task.result()

                    results[node] = result
                    if result is not None and result:
                        node_scope[node] = result.unwrap()

                    i = index_by_node[node]
                    with nogil:
                        self.native.mark_finished(state.state, <size_t> i)

                    if result is not None and kungfu.is_err(result) and node in self.final_nodes:
                        for task in running:
                            task.cancel()
                        raise result.error

                    progressed = True

            elif not progressed and pending:
                node = next(iter(pending))
                raise NodeError(f"could not resolve dependencies of `{node.__name__}`")


__all__ = ("AsyncNodinaAgent", "NodinaAgent", "backend_name")

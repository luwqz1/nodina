from libc.stddef cimport size_t
from libc.stdint cimport uint32_t, uint64_t


cdef extern from "core/nodina_core.h":
    ctypedef struct nodina_mutex:
        pass

    ctypedef struct nodina_plan_node:
        void *node
        size_t dep_count
        size_t *deps
        uint32_t flags

    ctypedef struct nodina_plan:
        size_t len
        nodina_plan_node *nodes

    ctypedef struct nodina_run_state:
        size_t len
        unsigned char *started
        unsigned char *finished

    ctypedef struct nodina_uv_runner:
        pass

    ctypedef struct nodina_uv_work:
        pass

    ctypedef void (*nodina_uv_work_cb)(void *data) noexcept with gil
    ctypedef void (*nodina_uv_after_work_cb)(void *data, int status) noexcept with gil

    int NODINA_NODE_PLAIN
    int NODINA_NODE_EITHER
    int NODINA_NODE_CONCURRENT_EITHER
    int NODINA_NODE_RESULT

    int nodina_mutex_new(nodina_mutex **out) nogil
    void nodina_mutex_free(nodina_mutex *mutex) nogil
    void nodina_mutex_lock(nodina_mutex *mutex) nogil
    void nodina_mutex_unlock(nodina_mutex *mutex) nogil

    int nodina_plan_new(size_t len, nodina_plan **out) nogil
    void nodina_plan_free(nodina_plan *plan) nogil
    int nodina_plan_set_node(
        nodina_plan *plan,
        size_t index,
        void *node,
        const size_t *deps,
        size_t dep_count,
        uint32_t flags,
    ) nogil

    int nodina_run_state_new(size_t len, nodina_run_state **out) nogil
    void nodina_run_state_free(nodina_run_state *state) nogil
    int nodina_run_state_mark_started(nodina_run_state *state, size_t index) nogil
    int nodina_run_state_mark_finished(nodina_run_state *state, size_t index) nogil
    int nodina_run_state_is_finished(const nodina_run_state *state, size_t index) nogil

    int nodina_uv_available() nogil
    const char *nodina_uv_backend_name() nogil

    int nodina_uv_runner_new(nodina_uv_runner **out) nogil
    void nodina_uv_runner_free(nodina_uv_runner *runner) nogil
    int nodina_uv_runner_run_once(nodina_uv_runner *runner) nogil
    int nodina_uv_runner_run_default(nodina_uv_runner *runner) nogil
    void nodina_uv_runner_stop(nodina_uv_runner *runner) nogil
    uint64_t nodina_uv_now(nodina_uv_runner *runner) nogil
    int nodina_uv_runner_sleep(nodina_uv_runner *runner, uint64_t timeout_ms) nogil
    int nodina_uv_runner_queue_work(
        nodina_uv_runner *runner,
        nodina_uv_work_cb work_cb,
        nodina_uv_after_work_cb after_cb,
        void *data,
        nodina_uv_work **out,
    ) nogil
    int nodina_uv_work_is_done(const nodina_uv_work *work) nogil
    int nodina_uv_work_status(const nodina_uv_work *work) nogil
    void nodina_uv_work_free(nodina_uv_work *work) nogil

from libc.stddef cimport size_t


cdef extern from "core/nodina_core.h":
    ctypedef struct nodina_uv_runner:
        pass

    ctypedef struct nodina_uv_work:
        pass

    ctypedef void (*nodina_uv_work_cb)(void *data) noexcept with gil
    ctypedef void (*nodina_uv_after_work_cb)(void *data, int status) noexcept with gil

    const char *nodina_uv_backend_name() nogil

    int nodina_uv_runner_new(nodina_uv_runner **out) nogil
    void nodina_uv_runner_free(nodina_uv_runner *runner) nogil
    int nodina_uv_runner_run_once(nodina_uv_runner *runner) nogil
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

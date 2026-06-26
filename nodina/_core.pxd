cdef extern from "core/nodina_pool.h" nogil:
    ctypedef void (*nodina_task_fn)(void *data) noexcept with gil

    ctypedef struct nodina_pool:
        pass

    nodina_pool *nodina_pool_new(int n_threads)
    void nodina_pool_free(nodina_pool *pool)
    int nodina_pool_submit(nodina_pool *pool, nodina_task_fn fn, void *data)
    unsigned long long nodina_pool_completed(nodina_pool *pool)
    void nodina_pool_wait(nodina_pool *pool, unsigned long long *cursor)
    int nodina_pool_threads(const nodina_pool *pool)

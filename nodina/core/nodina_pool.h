#ifndef NODINA_POOL_H
#define NODINA_POOL_H

/*
 * A tiny fixed-size pthread work pool with no libuv and no busy-polling.
 *
 * All queueing / waiting runs WITHOUT the GIL; the only place the GIL is taken
 * is inside the task callback (a Cython `noexcept with gil` function), and only
 * for as long as the Python `__compose__` runs. On a normal CPython build that
 * still serializes Python work on the GIL (blocking nodes overlap, CPU nodes do
 * not). On a free-threaded build (PEP 703) the callbacks run truly in parallel.
 */

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*nodina_task_fn)(void *data);

typedef struct nodina_pool nodina_pool;

/* Create a pool with `n_threads` workers (>=1). Returns NULL on failure. */
nodina_pool *nodina_pool_new(int n_threads);

/* Stop all workers (joins them) and free the pool. */
void nodina_pool_free(nodina_pool *pool);

/* Enqueue `fn(data)`. Returns 0 on success, -1 on allocation failure. nogil. */
int nodina_pool_submit(nodina_pool *pool, nodina_task_fn fn, void *data);

/* Current monotonic count of completed tasks (takes the lock briefly). */
unsigned long long nodina_pool_completed(nodina_pool *pool);

/* Block until the completion counter differs from *cursor, then store the new
 * value in *cursor. Used to wait for "some task finished" without busy-polling.
 * Safe to call from several threads at once (each gets its own cursor). nogil. */
void nodina_pool_wait(nodina_pool *pool, unsigned long long *cursor);

/* Number of worker threads actually started. */
int nodina_pool_threads(const nodina_pool *pool);

#ifdef __cplusplus
}
#endif

#endif

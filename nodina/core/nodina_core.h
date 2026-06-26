#ifndef NODINA_CORE_H
#define NODINA_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#include <uv.h>
typedef struct nodina_mutex {
    uv_mutex_t inner;
} nodina_mutex;

typedef enum nodina_node_flags {
    NODINA_NODE_PLAIN = 0,
    NODINA_NODE_EITHER = 1u << 0u,
    NODINA_NODE_CONCURRENT_EITHER = 1u << 1u,
    NODINA_NODE_RESULT = 1u << 2u,
} nodina_node_flags;

typedef struct nodina_plan_node {
    void *node;
    size_t dep_count;
    size_t *deps;
    uint32_t flags;
} nodina_plan_node;

typedef struct nodina_plan {
    size_t len;
    nodina_plan_node *nodes;
} nodina_plan;

typedef struct nodina_run_state {
    size_t len;
    uint8_t *started;
    uint8_t *finished;
} nodina_run_state;

int nodina_mutex_new(nodina_mutex **out);
void nodina_mutex_free(nodina_mutex *mutex);
int nodina_mutex_init(nodina_mutex *mutex);
void nodina_mutex_destroy(nodina_mutex *mutex);
void nodina_mutex_lock(nodina_mutex *mutex);
void nodina_mutex_unlock(nodina_mutex *mutex);

int nodina_plan_new(size_t len, nodina_plan **out);
void nodina_plan_free(nodina_plan *plan);
int nodina_plan_set_node(
    nodina_plan *plan,
    size_t index,
    void *node,
    const size_t *deps,
    size_t dep_count,
    uint32_t flags
);

int nodina_run_state_new(size_t len, nodina_run_state **out);
void nodina_run_state_free(nodina_run_state *state);
int nodina_run_state_mark_started(nodina_run_state *state, size_t index);
int nodina_run_state_mark_finished(nodina_run_state *state, size_t index);
int nodina_run_state_is_finished(const nodina_run_state *state, size_t index);

int nodina_uv_available(void);
const char *nodina_uv_backend_name(void);

typedef struct nodina_uv_runner nodina_uv_runner;
typedef struct nodina_uv_work nodina_uv_work;
typedef void (*nodina_uv_work_cb)(void *data);
typedef void (*nodina_uv_after_work_cb)(void *data, int status);

int nodina_uv_runner_new(nodina_uv_runner **out);
void nodina_uv_runner_free(nodina_uv_runner *runner);
int nodina_uv_runner_run_once(nodina_uv_runner *runner);
int nodina_uv_runner_run_default(nodina_uv_runner *runner);
void nodina_uv_runner_stop(nodina_uv_runner *runner);
uint64_t nodina_uv_now(nodina_uv_runner *runner);
int nodina_uv_runner_sleep(nodina_uv_runner *runner, uint64_t timeout_ms);
int nodina_uv_runner_queue_work(
    nodina_uv_runner *runner,
    nodina_uv_work_cb work_cb,
    nodina_uv_after_work_cb after_cb,
    void *data,
    nodina_uv_work **out
);
int nodina_uv_work_is_done(const nodina_uv_work *work);
int nodina_uv_work_status(const nodina_uv_work *work);
void nodina_uv_work_free(nodina_uv_work *work);

#ifdef __cplusplus
}
#endif

#endif

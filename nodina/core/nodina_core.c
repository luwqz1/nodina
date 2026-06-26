#include "nodina_core.h"

#include <stdlib.h>
#include <string.h>

int nodina_mutex_new(nodina_mutex **out)
{
    nodina_mutex *mutex;
    int rc;

    if (out == NULL) {
        return -1;
    }

    *out = NULL;
    mutex = (nodina_mutex *) calloc(1, sizeof(nodina_mutex));
    if (mutex == NULL) {
        return -2;
    }

    rc = nodina_mutex_init(mutex);
    if (rc != 0) {
        free(mutex);
        return rc;
    }

    *out = mutex;
    return 0;
}

void nodina_mutex_free(nodina_mutex *mutex)
{
    if (mutex == NULL) {
        return;
    }

    nodina_mutex_destroy(mutex);
    free(mutex);
}

int nodina_mutex_init(nodina_mutex *mutex)
{
    return uv_mutex_init(&mutex->inner);
}

void nodina_mutex_destroy(nodina_mutex *mutex)
{
    uv_mutex_destroy(&mutex->inner);
}

void nodina_mutex_lock(nodina_mutex *mutex)
{
    uv_mutex_lock(&mutex->inner);
}

void nodina_mutex_unlock(nodina_mutex *mutex)
{
    uv_mutex_unlock(&mutex->inner);
}

int nodina_plan_new(size_t len, nodina_plan **out)
{
    nodina_plan *plan;

    if (out == NULL) {
        return -1;
    }

    *out = NULL;
    plan = (nodina_plan *) calloc(1, sizeof(nodina_plan));
    if (plan == NULL) {
        return -2;
    }

    if (len > 0) {
        plan->nodes = (nodina_plan_node *) calloc(len, sizeof(nodina_plan_node));
        if (plan->nodes == NULL) {
            free(plan);
            return -2;
        }
    }

    plan->len = len;
    *out = plan;
    return 0;
}

void nodina_plan_free(nodina_plan *plan)
{
    size_t i;

    if (plan == NULL) {
        return;
    }

    for (i = 0; i < plan->len; i++) {
        free(plan->nodes[i].deps);
    }

    free(plan->nodes);
    free(plan);
}

int nodina_plan_set_node(
    nodina_plan *plan,
    size_t index,
    void *node,
    const size_t *deps,
    size_t dep_count,
    uint32_t flags
)
{
    nodina_plan_node *target;
    size_t *copied_deps = NULL;

    if (plan == NULL || index >= plan->len) {
        return -1;
    }

    if (dep_count > 0) {
        if (deps == NULL) {
            return -1;
        }

        copied_deps = (size_t *) malloc(sizeof(size_t) * dep_count);
        if (copied_deps == NULL) {
            return -2;
        }
        memcpy(copied_deps, deps, sizeof(size_t) * dep_count);
    }

    target = &plan->nodes[index];
    free(target->deps);
    target->node = node;
    target->deps = copied_deps;
    target->dep_count = dep_count;
    target->flags = flags;
    return 0;
}

int nodina_run_state_new(size_t len, nodina_run_state **out)
{
    nodina_run_state *state;

    if (out == NULL) {
        return -1;
    }

    *out = NULL;
    state = (nodina_run_state *) calloc(1, sizeof(nodina_run_state));
    if (state == NULL) {
        return -2;
    }

    if (len > 0) {
        state->started = (uint8_t *) calloc(len, sizeof(uint8_t));
        state->finished = (uint8_t *) calloc(len, sizeof(uint8_t));
        if (state->started == NULL || state->finished == NULL) {
            free(state->started);
            free(state->finished);
            free(state);
            return -2;
        }
    }

    state->len = len;
    *out = state;
    return 0;
}

void nodina_run_state_free(nodina_run_state *state)
{
    if (state == NULL) {
        return;
    }

    free(state->started);
    free(state->finished);
    free(state);
}

int nodina_run_state_mark_started(nodina_run_state *state, size_t index)
{
    if (state == NULL || index >= state->len) {
        return -1;
    }

    state->started[index] = 1;
    return 0;
}

int nodina_run_state_mark_finished(nodina_run_state *state, size_t index)
{
    if (state == NULL || index >= state->len) {
        return -1;
    }

    state->finished[index] = 1;
    return 0;
}

int nodina_run_state_is_finished(const nodina_run_state *state, size_t index)
{
    if (state == NULL || index >= state->len) {
        return 0;
    }

    return state->finished[index] != 0;
}

int nodina_uv_available(void)
{
    return 1;
}

const char *nodina_uv_backend_name(void)
{
    return "libuv";
}

struct nodina_uv_runner {
    uv_loop_t loop;
    int initialized;
};

struct nodina_uv_work {
    uv_work_t request;
    nodina_uv_work_cb work_cb;
    nodina_uv_after_work_cb after_cb;
    void *data;
    int done;
    int status;
};

static void nodina_uv_close_walk_cb(uv_handle_t *handle, void *arg)
{
    (void) arg;
    if (!uv_is_closing(handle)) {
        uv_close(handle, NULL);
    }
}

int nodina_uv_runner_new(nodina_uv_runner **out)
{
    nodina_uv_runner *runner;
    int rc;

    if (out == NULL) {
        return -1;
    }

    *out = NULL;
    runner = (nodina_uv_runner *) calloc(1, sizeof(nodina_uv_runner));
    if (runner == NULL) {
        return -2;
    }

    rc = uv_loop_init(&runner->loop);
    if (rc != 0) {
        free(runner);
        return rc;
    }

    runner->initialized = 1;
    *out = runner;
    return 0;
}

void nodina_uv_runner_free(nodina_uv_runner *runner)
{
    int rc;

    if (runner == NULL) {
        return;
    }

    if (runner->initialized) {
        uv_walk(&runner->loop, nodina_uv_close_walk_cb, NULL);
        (void) uv_run(&runner->loop, UV_RUN_DEFAULT);
        rc = uv_loop_close(&runner->loop);
        if (rc == UV_EBUSY) {
            uv_stop(&runner->loop);
            (void) uv_run(&runner->loop, UV_RUN_NOWAIT);
            (void) uv_loop_close(&runner->loop);
        }
    }

    free(runner);
}

int nodina_uv_runner_run_once(nodina_uv_runner *runner)
{
    if (runner == NULL) {
        return -1;
    }

    return uv_run(&runner->loop, UV_RUN_ONCE);
}

int nodina_uv_runner_run_default(nodina_uv_runner *runner)
{
    if (runner == NULL) {
        return -1;
    }

    return uv_run(&runner->loop, UV_RUN_DEFAULT);
}

void nodina_uv_runner_stop(nodina_uv_runner *runner)
{
    if (runner == NULL) {
        return;
    }

    uv_stop(&runner->loop);
}

uint64_t nodina_uv_now(nodina_uv_runner *runner)
{
    if (runner == NULL) {
        return 0;
    }

    uv_update_time(&runner->loop);
    return uv_now(&runner->loop);
}

typedef struct nodina_sleep_req {
    uv_timer_t timer;
    int done;
} nodina_sleep_req;

static void nodina_uv_sleep_close_cb(uv_handle_t *handle)
{
    nodina_sleep_req *request = (nodina_sleep_req *) handle->data;
    free(request);
}

static void nodina_uv_sleep_cb(uv_timer_t *timer)
{
    nodina_sleep_req *request = (nodina_sleep_req *) timer->data;
    request->done = 1;
    uv_timer_stop(timer);
    uv_close((uv_handle_t *) timer, nodina_uv_sleep_close_cb);
}

int nodina_uv_runner_sleep(nodina_uv_runner *runner, uint64_t timeout_ms)
{
    nodina_sleep_req *request;
    int rc;

    if (runner == NULL) {
        return -1;
    }

    request = (nodina_sleep_req *) calloc(1, sizeof(nodina_sleep_req));
    if (request == NULL) {
        return -2;
    }

    request->timer.data = request;
    rc = uv_timer_init(&runner->loop, &request->timer);
    if (rc != 0) {
        free(request);
        return rc;
    }

    rc = uv_timer_start(&request->timer, nodina_uv_sleep_cb, timeout_ms, 0);
    if (rc != 0) {
        uv_close((uv_handle_t *) &request->timer, nodina_uv_sleep_close_cb);
        (void) uv_run(&runner->loop, UV_RUN_DEFAULT);
        return rc;
    }

    while (!request->done) {
        rc = uv_run(&runner->loop, UV_RUN_ONCE);
        if (rc < 0) {
            return rc;
        }
    }

    (void) uv_run(&runner->loop, UV_RUN_NOWAIT);
    return 0;
}

static void nodina_uv_work_cb_dispatch(uv_work_t *request)
{
    nodina_uv_work *work = (nodina_uv_work *) request->data;

    if (work->work_cb != NULL) {
        work->work_cb(work->data);
    }
}

static void nodina_uv_after_work_cb_dispatch(uv_work_t *request, int status)
{
    nodina_uv_work *work = (nodina_uv_work *) request->data;

    work->status = status;
    work->done = 1;

    if (work->after_cb != NULL) {
        work->after_cb(work->data, status);
    }
}

int nodina_uv_runner_queue_work(
    nodina_uv_runner *runner,
    nodina_uv_work_cb work_cb,
    nodina_uv_after_work_cb after_cb,
    void *data,
    nodina_uv_work **out
)
{
    nodina_uv_work *work;
    int rc;

    if (runner == NULL || work_cb == NULL || out == NULL) {
        return -1;
    }

    *out = NULL;
    work = (nodina_uv_work *) calloc(1, sizeof(nodina_uv_work));
    if (work == NULL) {
        return -2;
    }

    work->request.data = work;
    work->work_cb = work_cb;
    work->after_cb = after_cb;
    work->data = data;

    rc = uv_queue_work(&runner->loop, &work->request, nodina_uv_work_cb_dispatch, nodina_uv_after_work_cb_dispatch);
    if (rc != 0) {
        free(work);
        return rc;
    }

    *out = work;
    return 0;
}

int nodina_uv_work_is_done(const nodina_uv_work *work)
{
    if (work == NULL) {
        return 0;
    }

    return work->done != 0;
}

int nodina_uv_work_status(const nodina_uv_work *work)
{
    if (work == NULL) {
        return -1;
    }

    return work->status;
}

void nodina_uv_work_free(nodina_uv_work *work)
{
    free(work);
}

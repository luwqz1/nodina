#include "nodina_core.h"

#include <stdlib.h>

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

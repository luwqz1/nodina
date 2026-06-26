#ifndef NODINA_CORE_H
#define NODINA_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#include <uv.h>

const char *nodina_uv_backend_name(void);

typedef struct nodina_uv_runner nodina_uv_runner;
typedef struct nodina_uv_work nodina_uv_work;
typedef void (*nodina_uv_work_cb)(void *data);
typedef void (*nodina_uv_after_work_cb)(void *data, int status);

int nodina_uv_runner_new(nodina_uv_runner **out);
void nodina_uv_runner_free(nodina_uv_runner *runner);
int nodina_uv_runner_run_once(nodina_uv_runner *runner);
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

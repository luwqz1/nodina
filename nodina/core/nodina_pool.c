#include "nodina_pool.h"

#include <pthread.h>
#include <stdlib.h>

typedef struct nodina_task {
    nodina_task_fn fn;
    void *data;
    struct nodina_task *next;
} nodina_task;

struct nodina_pool {
    pthread_mutex_t mutex;
    pthread_cond_t work_cv;   /* signaled when a task is queued */
    pthread_cond_t done_cv;   /* broadcast when a task completes */
    nodina_task *head;
    nodina_task *tail;
    pthread_t *threads;
    int n_threads;
    int shutdown;
    unsigned long long completed;
};

static void *nodina_pool_worker(void *arg)
{
    nodina_pool *pool = (nodina_pool *) arg;

    for (;;) {
        pthread_mutex_lock(&pool->mutex);
        while (pool->head == NULL && !pool->shutdown) {
            pthread_cond_wait(&pool->work_cv, &pool->mutex);
        }
        if (pool->shutdown && pool->head == NULL) {
            pthread_mutex_unlock(&pool->mutex);
            return NULL;
        }

        nodina_task *task = pool->head;
        pool->head = task->next;
        if (pool->head == NULL) {
            pool->tail = NULL;
        }
        pthread_mutex_unlock(&pool->mutex);

        task->fn(task->data);   /* acquires the GIL internally, only for the call */
        free(task);

        pthread_mutex_lock(&pool->mutex);
        pool->completed++;
        pthread_cond_broadcast(&pool->done_cv);
        pthread_mutex_unlock(&pool->mutex);
    }
}

static void nodina_pool_destroy_sync(nodina_pool *pool)
{
    pthread_cond_destroy(&pool->done_cv);
    pthread_cond_destroy(&pool->work_cv);
    pthread_mutex_destroy(&pool->mutex);
}

nodina_pool *nodina_pool_new(int n_threads)
{
    int i;
    nodina_pool *pool;

    if (n_threads < 1) {
        n_threads = 1;
    }

    pool = (nodina_pool *) calloc(1, sizeof(*pool));
    if (pool == NULL) {
        return NULL;
    }

    if (pthread_mutex_init(&pool->mutex, NULL) != 0) {
        free(pool);
        return NULL;
    }
    if (pthread_cond_init(&pool->work_cv, NULL) != 0) {
        pthread_mutex_destroy(&pool->mutex);
        free(pool);
        return NULL;
    }
    if (pthread_cond_init(&pool->done_cv, NULL) != 0) {
        pthread_cond_destroy(&pool->work_cv);
        pthread_mutex_destroy(&pool->mutex);
        free(pool);
        return NULL;
    }

    pool->threads = (pthread_t *) calloc((size_t) n_threads, sizeof(pthread_t));
    if (pool->threads == NULL) {
        nodina_pool_destroy_sync(pool);
        free(pool);
        return NULL;
    }

    pool->n_threads = 0;
    for (i = 0; i < n_threads; i++) {
        if (pthread_create(&pool->threads[i], NULL, nodina_pool_worker, pool) != 0) {
            break;
        }
        pool->n_threads++;
    }

    if (pool->n_threads == 0) {
        free(pool->threads);
        nodina_pool_destroy_sync(pool);
        free(pool);
        return NULL;
    }

    return pool;
}

int nodina_pool_submit(nodina_pool *pool, nodina_task_fn fn, void *data)
{
    nodina_task *task;

    if (pool == NULL || fn == NULL) {
        return -1;
    }

    task = (nodina_task *) malloc(sizeof(*task));
    if (task == NULL) {
        return -1;
    }
    task->fn = fn;
    task->data = data;
    task->next = NULL;

    pthread_mutex_lock(&pool->mutex);
    if (pool->tail != NULL) {
        pool->tail->next = task;
    } else {
        pool->head = task;
    }
    pool->tail = task;
    pthread_cond_signal(&pool->work_cv);
    pthread_mutex_unlock(&pool->mutex);

    return 0;
}

unsigned long long nodina_pool_completed(nodina_pool *pool)
{
    unsigned long long value;

    pthread_mutex_lock(&pool->mutex);
    value = pool->completed;
    pthread_mutex_unlock(&pool->mutex);

    return value;
}

void nodina_pool_wait(nodina_pool *pool, unsigned long long *cursor)
{
    pthread_mutex_lock(&pool->mutex);
    while (pool->completed == *cursor) {
        pthread_cond_wait(&pool->done_cv, &pool->mutex);
    }
    *cursor = pool->completed;
    pthread_mutex_unlock(&pool->mutex);
}

int nodina_pool_threads(const nodina_pool *pool)
{
    return pool->n_threads;
}

void nodina_pool_free(nodina_pool *pool)
{
    int i;
    nodina_task *task;

    if (pool == NULL) {
        return;
    }

    pthread_mutex_lock(&pool->mutex);
    pool->shutdown = 1;
    pthread_cond_broadcast(&pool->work_cv);
    pthread_mutex_unlock(&pool->mutex);

    for (i = 0; i < pool->n_threads; i++) {
        pthread_join(pool->threads[i], NULL);
    }

    task = pool->head;
    while (task != NULL) {
        nodina_task *next = task->next;
        free(task);
        task = next;
    }

    free(pool->threads);
    nodina_pool_destroy_sync(pool);
    free(pool);
}

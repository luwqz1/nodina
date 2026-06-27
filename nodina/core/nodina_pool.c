#include "nodina_pool.h"

#include <stdlib.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <pthread.h>
#endif

typedef struct nodina_task {
    nodina_task_fn fn;
    void *data;
    struct nodina_task *next;
} nodina_task;

struct nodina_pool {
#ifdef _WIN32
    CRITICAL_SECTION mutex;
    CONDITION_VARIABLE work_cv;   /* signaled when a task is queued */
    CONDITION_VARIABLE done_cv;   /* broadcast when a task completes */
    HANDLE *threads;
#else
    pthread_mutex_t mutex;
    pthread_cond_t work_cv;   /* signaled when a task is queued */
    pthread_cond_t done_cv;   /* broadcast when a task completes */
    pthread_t *threads;
#endif
    nodina_task *head;
    nodina_task *tail;
    int n_threads;
    int shutdown;
    unsigned long long completed;
};

#ifdef _WIN32
static DWORD WINAPI nodina_pool_worker(void *arg)
#else
static void *nodina_pool_worker(void *arg)
#endif
{
    nodina_pool *pool = (nodina_pool *) arg;

    for (;;) {
#ifdef _WIN32
        EnterCriticalSection(&pool->mutex);
        while (pool->head == NULL && !pool->shutdown) {
            SleepConditionVariableCS(&pool->work_cv, &pool->mutex, INFINITE);
        }
        if (pool->shutdown && pool->head == NULL) {
            LeaveCriticalSection(&pool->mutex);
            return 0;
        }
#else
        pthread_mutex_lock(&pool->mutex);
        while (pool->head == NULL && !pool->shutdown) {
            pthread_cond_wait(&pool->work_cv, &pool->mutex);
        }
        if (pool->shutdown && pool->head == NULL) {
            pthread_mutex_unlock(&pool->mutex);
            return NULL;
        }
#endif

        nodina_task *task = pool->head;
        pool->head = task->next;
        if (pool->head == NULL) {
            pool->tail = NULL;
        }
#ifdef _WIN32
        LeaveCriticalSection(&pool->mutex);
#else
        pthread_mutex_unlock(&pool->mutex);
#endif

        task->fn(task->data);   /* acquires the GIL internally, only for the call */
        free(task);

#ifdef _WIN32
        EnterCriticalSection(&pool->mutex);
        pool->completed++;
        WakeAllConditionVariable(&pool->done_cv);
        LeaveCriticalSection(&pool->mutex);
#else
        pthread_mutex_lock(&pool->mutex);
        pool->completed++;
        pthread_cond_broadcast(&pool->done_cv);
        pthread_mutex_unlock(&pool->mutex);
#endif
    }
}

static void nodina_pool_destroy_sync(nodina_pool *pool)
{
#ifdef _WIN32
    DeleteCriticalSection(&pool->mutex);
#else
    pthread_cond_destroy(&pool->done_cv);
    pthread_cond_destroy(&pool->work_cv);
    pthread_mutex_destroy(&pool->mutex);
#endif
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

#ifdef _WIN32
    InitializeCriticalSection(&pool->mutex);
    InitializeConditionVariable(&pool->work_cv);
    InitializeConditionVariable(&pool->done_cv);
#else
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
#endif

#ifdef _WIN32
    pool->threads = (HANDLE *) calloc((size_t) n_threads, sizeof(HANDLE));
#else
    pool->threads = (pthread_t *) calloc((size_t) n_threads, sizeof(pthread_t));
#endif
    if (pool->threads == NULL) {
        nodina_pool_destroy_sync(pool);
        free(pool);
        return NULL;
    }

    pool->n_threads = 0;
    for (i = 0; i < n_threads; i++) {
#ifdef _WIN32
        pool->threads[i] = CreateThread(NULL, 0, nodina_pool_worker, pool, 0, NULL);
        if (pool->threads[i] == NULL) {
            break;
        }
#else
        if (pthread_create(&pool->threads[i], NULL, nodina_pool_worker, pool) != 0) {
            break;
        }
#endif
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

#ifdef _WIN32
    EnterCriticalSection(&pool->mutex);
    if (pool->tail != NULL) {
        pool->tail->next = task;
    } else {
        pool->head = task;
    }
    pool->tail = task;
    WakeConditionVariable(&pool->work_cv);
    LeaveCriticalSection(&pool->mutex);
#else
    pthread_mutex_lock(&pool->mutex);
    if (pool->tail != NULL) {
        pool->tail->next = task;
    } else {
        pool->head = task;
    }
    pool->tail = task;
    pthread_cond_signal(&pool->work_cv);
    pthread_mutex_unlock(&pool->mutex);
#endif

    return 0;
}

unsigned long long nodina_pool_completed(nodina_pool *pool)
{
    unsigned long long value;

#ifdef _WIN32
    EnterCriticalSection(&pool->mutex);
    value = pool->completed;
    LeaveCriticalSection(&pool->mutex);
#else
    pthread_mutex_lock(&pool->mutex);
    value = pool->completed;
    pthread_mutex_unlock(&pool->mutex);
#endif

    return value;
}

void nodina_pool_wait(nodina_pool *pool, unsigned long long *cursor)
{
#ifdef _WIN32
    EnterCriticalSection(&pool->mutex);
    while (pool->completed == *cursor) {
        SleepConditionVariableCS(&pool->done_cv, &pool->mutex, INFINITE);
    }
    *cursor = pool->completed;
    LeaveCriticalSection(&pool->mutex);
#else
    pthread_mutex_lock(&pool->mutex);
    while (pool->completed == *cursor) {
        pthread_cond_wait(&pool->done_cv, &pool->mutex);
    }
    *cursor = pool->completed;
    pthread_mutex_unlock(&pool->mutex);
#endif
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

#ifdef _WIN32
    EnterCriticalSection(&pool->mutex);
    pool->shutdown = 1;
    WakeAllConditionVariable(&pool->work_cv);
    LeaveCriticalSection(&pool->mutex);

    for (i = 0; i < pool->n_threads; i++) {
        WaitForSingleObject(pool->threads[i], INFINITE);
        CloseHandle(pool->threads[i]);
    }
#else
    pthread_mutex_lock(&pool->mutex);
    pool->shutdown = 1;
    pthread_cond_broadcast(&pool->work_cv);
    pthread_mutex_unlock(&pool->mutex);

    for (i = 0; i < pool->n_threads; i++) {
        pthread_join(pool->threads[i], NULL);
    }
#endif

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

/* Thin portability layer: POSIX threads on Linux/macOS, Win32 primitives on Windows. Header-only. */
#ifndef EBMS_SYNC_H
#define EBMS_SYNC_H

#include "common.h"

typedef void (*ebms_entry_fn)(void *arg);

typedef struct {
    ebms_entry_fn fn;
    void *arg;
} ebms_thread_ctx;

#ifdef _WIN32
/* condition variables need Windows Vista or newer */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <windows.h>
typedef CRITICAL_SECTION ebms_mutex_t;
typedef CONDITION_VARIABLE ebms_cond_t;
typedef HANDLE ebms_thread_t;

static inline void ebms_mutex_init(ebms_mutex_t *m) { InitializeCriticalSection(m); }
static inline void ebms_mutex_destroy(ebms_mutex_t *m) { DeleteCriticalSection(m); }
static inline void ebms_mutex_lock(ebms_mutex_t *m) { EnterCriticalSection(m); }
static inline void ebms_mutex_unlock(ebms_mutex_t *m) { LeaveCriticalSection(m); }
static inline void ebms_cond_init(ebms_cond_t *c) { InitializeConditionVariable(c); }
static inline void ebms_cond_destroy(ebms_cond_t *c) { (void)c; }
static inline void ebms_cond_wait(ebms_cond_t *c, ebms_mutex_t *m) { SleepConditionVariableCS(c, m, INFINITE); }
static inline void ebms_cond_signal(ebms_cond_t *c) { WakeConditionVariable(c); }
static inline void ebms_cond_broadcast(ebms_cond_t *c) { WakeAllConditionVariable(c); }
static inline void ebms_sleep_ms(unsigned ms) { Sleep(ms); }

static inline DWORD WINAPI ebms_tramp(LPVOID p) {
    ebms_thread_ctx c = *(ebms_thread_ctx *)p;
    free(p);
    c.fn(c.arg);
    return 0;
}
static inline int ebms_thread_create(ebms_thread_t *t, ebms_entry_fn fn, void *arg) {
    ebms_thread_ctx *c = malloc(sizeof *c);
    if (!c) return -1;
    c->fn = fn;
    c->arg = arg;
    *t = CreateThread(NULL, 0, ebms_tramp, c, 0, NULL);
    if (!*t) {
        free(c);
        return -1;
    }
    return 0;
}
static inline int ebms_thread_join(ebms_thread_t t) {
    WaitForSingleObject(t, INFINITE);
    CloseHandle(t);
    return 0;
}
#else
#include <pthread.h>
#include <time.h>
typedef pthread_mutex_t ebms_mutex_t;
typedef pthread_cond_t ebms_cond_t;
typedef pthread_t ebms_thread_t;

static inline void ebms_mutex_init(ebms_mutex_t *m) { pthread_mutex_init(m, NULL); }
static inline void ebms_mutex_destroy(ebms_mutex_t *m) { pthread_mutex_destroy(m); }
static inline void ebms_mutex_lock(ebms_mutex_t *m) { pthread_mutex_lock(m); }
static inline void ebms_mutex_unlock(ebms_mutex_t *m) { pthread_mutex_unlock(m); }
static inline void ebms_cond_init(ebms_cond_t *c) { pthread_cond_init(c, NULL); }
static inline void ebms_cond_destroy(ebms_cond_t *c) { pthread_cond_destroy(c); }
static inline void ebms_cond_wait(ebms_cond_t *c, ebms_mutex_t *m) { pthread_cond_wait(c, m); }
static inline void ebms_cond_signal(ebms_cond_t *c) { pthread_cond_signal(c); }
static inline void ebms_cond_broadcast(ebms_cond_t *c) { pthread_cond_broadcast(c); }
static inline void ebms_sleep_ms(unsigned ms) {
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

static inline void *ebms_tramp(void *p) {
    ebms_thread_ctx c = *(ebms_thread_ctx *)p;
    free(p);
    c.fn(c.arg);
    return NULL;
}
static inline int ebms_thread_create(ebms_thread_t *t, ebms_entry_fn fn, void *arg) {
    ebms_thread_ctx *c = malloc(sizeof *c);
    if (!c) return -1;
    c->fn = fn;
    c->arg = arg;
    if (pthread_create(t, NULL, ebms_tramp, c) != 0) {
        free(c);
        return -1;
    }
    return 0;
}
static inline int ebms_thread_join(ebms_thread_t t) { return pthread_join(t, NULL) == 0 ? 0 : -1; }
#endif

#endif

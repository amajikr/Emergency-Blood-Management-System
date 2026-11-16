/*
 * Writer-preference readers-writers lock.
 *   - a reader may enter only if no writer is active AND no writer is waiting
 *   - a writer may enter only if no reader and no writer is active
 *   - on write unlock, one waiting writer is woken if any; otherwise all waiting readers are released
 * Consequence (documented): a continuous stream of writers can starve readers. Readers are only guaranteed
 * progress once the writer queue drains. Readers can overlap with each other, never with a writer.
 */
#include "readers_writers.h"

void rw_init(RWState *s) {
    memset(s, 0, sizeof *s);
    ebms_mutex_init(&s->mu);
    ebms_cond_init(&s->can_read);
    ebms_cond_init(&s->can_write);
}

void rw_destroy(RWState *s) {
    ebms_cond_destroy(&s->can_read);
    ebms_cond_destroy(&s->can_write);
    ebms_mutex_destroy(&s->mu);
}

void rw_read_lock(RWState *s) {
    ebms_mutex_lock(&s->mu);
    while (s->writer_active || s->writers_waiting > 0) ebms_cond_wait(&s->can_read, &s->mu);
    if (s->writer_active) s->violations++; /* unreachable with a correct lock */
    s->readers_active++;
    if (s->readers_active > s->max_readers) s->max_readers = s->readers_active;
    ebms_mutex_unlock(&s->mu);
}

void rw_read_unlock(RWState *s) {
    ebms_mutex_lock(&s->mu);
    s->readers_active--;
    s->reads_done++;
    if (s->readers_active == 0) ebms_cond_signal(&s->can_write);
    ebms_mutex_unlock(&s->mu);
}

void rw_write_lock(RWState *s) {
    ebms_mutex_lock(&s->mu);
    s->writers_waiting++;
    while (s->writer_active || s->readers_active > 0) ebms_cond_wait(&s->can_write, &s->mu);
    s->writers_waiting--;
    if (s->writer_active || s->readers_active > 0) s->violations++; /* unreachable with a correct lock */
    s->writer_active = 1;
    ebms_mutex_unlock(&s->mu);
}

void rw_write_unlock(RWState *s) {
    ebms_mutex_lock(&s->mu);
    s->writer_active = 0;
    s->writes_done++;
    s->version++;
    if (s->writers_waiting > 0) ebms_cond_signal(&s->can_write);
    else ebms_cond_broadcast(&s->can_read);
    ebms_mutex_unlock(&s->mu);
}

typedef struct {
    RWState *s;
    const RWConfig *cfg;
    long long torn;
} RWWorker;

static void reader_main(void *arg) {
    RWWorker *w = (RWWorker *)arg;
    int k;
    for (k = 0; k < w->cfg->reads_per_reader; k++) {
        long long x, y;
        rw_read_lock(w->s);
        x = w->s->a;
        if (w->cfg->hold_ms) ebms_sleep_ms((unsigned)w->cfg->hold_ms);
        y = w->s->b;
        if (x != y) w->torn++;
        rw_read_unlock(w->s);
    }
}

static void writer_main(void *arg) {
    RWWorker *w = (RWWorker *)arg;
    int k;
    for (k = 0; k < w->cfg->writes_per_writer; k++) {
        rw_write_lock(w->s);
        w->s->a = w->s->version + 1;
        if (w->cfg->hold_ms) ebms_sleep_ms((unsigned)w->cfg->hold_ms);
        w->s->b = w->s->version + 1;
        rw_write_unlock(w->s);
    }
}

int rw_simulate(const RWConfig *cfg, RWResult *res, char *err, size_t errsz) {
    RWState st;
    RWWorker *workers = NULL;
    ebms_thread_t *threads = NULL;
    int total_threads, i, created = 0, rc = RC_OK;
    long long serial = ((long long)cfg->readers * cfg->reads_per_reader +
                        (long long)cfg->writers * cfg->writes_per_writer) * cfg->hold_ms;

    memset(res, 0, sizeof *res);
    if (cfg->readers < 0 || cfg->readers > RW_MAX_THREADS || cfg->writers < 0 || cfg->writers > RW_MAX_THREADS ||
        cfg->readers + cfg->writers < 1) {
        set_error(err, errsz, "readers and writers must each be 0-16, with at least one thread");
        return RC_INVALID;
    }
    if (cfg->reads_per_reader < 0 || cfg->reads_per_reader > RW_MAX_OPS || cfg->writes_per_writer < 0 ||
        cfg->writes_per_writer > RW_MAX_OPS) {
        set_error(err, errsz, "operations per thread must be between 0 and 1000");
        return RC_INVALID;
    }
    if (cfg->hold_ms < 0 || cfg->hold_ms > RW_MAX_HOLD_MS) {
        set_error(err, errsz, "hold_ms must be between 0 and 50");
        return RC_INVALID;
    }
    if (serial > RW_MAX_SERIAL_MS) {
        set_error(err, errsz, "simulated work is too large: reduce operations or hold_ms");
        return RC_INVALID;
    }

    total_threads = cfg->readers + cfg->writers;
    workers = calloc((size_t)total_threads, sizeof *workers);
    threads = calloc((size_t)total_threads, sizeof *threads);
    if (!workers || !threads) {
        set_error(err, errsz, "out of memory");
        free(workers);
        free(threads);
        return RC_INTERNAL;
    }
    rw_init(&st);
    for (i = 0; i < total_threads; i++) {
        workers[i].s = &st;
        workers[i].cfg = cfg;
        workers[i].torn = 0;
    }
    for (i = 0; i < total_threads; i++) {
        ebms_entry_fn fn = i < cfg->readers ? reader_main : writer_main;
        if (ebms_thread_create(&threads[i], fn, &workers[i]) != 0) {
            rc = RC_INTERNAL;
            set_error(err, errsz, "could not start threads");
            break;
        }
        created++;
    }
    for (i = 0; i < created; i++) {
        ebms_thread_join(threads[i]);
        res->threads_joined++;
    }
    for (i = 0; rc == RC_OK && i < total_threads; i++) res->torn_reads += workers[i].torn;
    if (rc == RC_OK) {
        res->reads_done = st.reads_done;
        res->writes_done = st.writes_done;
        res->max_readers = st.max_readers;
        res->violations = st.violations;
        res->final_version = st.version;
    }
    rw_destroy(&st);
    free(workers);
    free(threads);
    return rc;
}

int readers_writers_run(const JsonValue *input, Buf *out, char *err, size_t errsz) {
    RWConfig cfg;
    RWResult res;
    long long v;
    int rc;
    int ok_reads, ok_writes;

    if (json_int_field(input, "readers", 0, RW_MAX_THREADS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.readers = (int)v;
    if (json_int_field(input, "writers", 0, RW_MAX_THREADS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.writers = (int)v;
    if (json_int_field(input, "reads_per_reader", 0, RW_MAX_OPS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.reads_per_reader = (int)v;
    if (json_int_field(input, "writes_per_writer", 0, RW_MAX_OPS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.writes_per_writer = (int)v;
    if (json_int_field(input, "hold_ms", 0, RW_MAX_HOLD_MS, 1, 0, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.hold_ms = (int)v;

    rc = rw_simulate(&cfg, &res, err, errsz);
    if (rc != RC_OK) return rc;

    ok_reads = res.reads_done == (long long)cfg.readers * cfg.reads_per_reader;
    ok_writes = res.writes_done == (long long)cfg.writers * cfg.writes_per_writer;
    buf_printf(out,
        "{\"ok\":true,\"readers\":%d,\"writers\":%d,\"reads_expected\":%lld,\"reads_completed\":%lld,"
        "\"writes_expected\":%lld,\"writes_completed\":%lld,\"max_concurrent_readers\":%lld,"
        "\"writer_exclusion_violations\":%lld,\"torn_reads\":%lld,\"final_version\":%lld,"
        "\"all_operations_completed\":%s,\"threads_joined\":%d,\"terminated\":true,"
        "\"policy\":",
        cfg.readers, cfg.writers, (long long)cfg.readers * cfg.reads_per_reader, res.reads_done,
        (long long)cfg.writers * cfg.writes_per_writer, res.writes_done, res.max_readers, res.violations,
        res.torn_reads, res.final_version, (ok_reads && ok_writes) ? "true" : "false", res.threads_joined);
    buf_json_string(out, "writer preference: new readers wait while any writer is waiting; "
                         "a continuous stream of writers can starve readers");
    buf_puts(out, "}");
    return out->oom ? RC_INTERNAL : RC_OK;
}

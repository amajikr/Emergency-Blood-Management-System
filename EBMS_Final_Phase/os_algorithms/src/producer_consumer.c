/*
 * Bounded buffer. Synchronization design:
 *   - one mutex guards buf, head, tail, count and all statistics
 *   - not_full  : producers sleep while count == capacity
 *   - not_empty : consumers sleep while count == 0 and the buffer is still open
 *   - closed flag: main sets it only after every producer has been joined, so consumers drain the buffer and
 *     exit. An aborted flag lets producers exit if thread creation fails partway.
 * Every item is value = producer_id * items_per_producer + sequence, so duplicates, losses and per-producer
 * reordering can all be checked after the run.
 */
#include "producer_consumer.h"
#include "ebms_sync.h"

typedef struct {
    int capacity;
    int items_per_producer;
    long long total;
    long long *buf;
    int head, tail, count;
    int closed, aborted;
    ebms_mutex_t mu;
    ebms_cond_t not_full, not_empty;
    PCResult *res;
    unsigned char *seen;
    long long *last_seq;
} PCState;

typedef struct {
    PCState *st;
    int id;
} PCWorker;

static void producer_main(void *arg) {
    PCWorker *w = (PCWorker *)arg;
    PCState *st = w->st;
    long long seq;
    for (seq = 0; seq < st->items_per_producer; seq++) {
        long long v = (long long)w->id * st->items_per_producer + seq;
        ebms_mutex_lock(&st->mu);
        while (st->count == st->capacity && !st->aborted) {
            st->res->producer_waits_full++;
            ebms_cond_wait(&st->not_full, &st->mu);
        }
        if (st->aborted) {
            ebms_mutex_unlock(&st->mu);
            return;
        }
        st->buf[st->tail] = v;
        st->tail = (st->tail + 1) % st->capacity;
        st->count++;
        st->res->produced++;
        st->res->checksum_produced += v;
        st->res->per_producer[w->id]++;
        if (st->count > st->res->max_occupancy) st->res->max_occupancy = st->count;
        ebms_cond_signal(&st->not_empty);
        ebms_mutex_unlock(&st->mu);
    }
}

static void consumer_main(void *arg) {
    PCWorker *w = (PCWorker *)arg;
    PCState *st = w->st;
    for (;;) {
        long long v, p, seq;
        ebms_mutex_lock(&st->mu);
        while (st->count == 0 && !st->closed && !st->aborted) {
            st->res->consumer_waits_empty++;
            ebms_cond_wait(&st->not_empty, &st->mu);
        }
        if (st->count == 0) { /* closed (or aborted) and drained */
            ebms_mutex_unlock(&st->mu);
            return;
        }
        v = st->buf[st->head];
        st->head = (st->head + 1) % st->capacity;
        st->count--;
        st->res->consumed++;
        st->res->checksum_consumed += v;
        st->res->per_consumer[w->id]++;
        if (v < 0 || v >= st->total) {
            st->res->unknown_items++;
        } else {
            p = v / st->items_per_producer;
            seq = v % st->items_per_producer;
            if (st->seen[v]) st->res->duplicates++;
            st->seen[v] = 1;
            if (seq <= st->last_seq[p]) st->res->order_violations++;
            st->last_seq[p] = seq;
        }
        ebms_cond_signal(&st->not_full);
        ebms_mutex_unlock(&st->mu);
    }
}

void pc_result_free(PCResult *res) {
    free(res->per_producer);
    free(res->per_consumer);
    memset(res, 0, sizeof *res);
}

int pc_simulate(const PCConfig *cfg, PCResult *res, char *err, size_t errsz) {
    PCState st;
    PCWorker *pw = NULL, *cw = NULL;
    ebms_thread_t *pthreads = NULL, *cthreads = NULL;
    int i, created_p = 0, created_c = 0, rc = RC_OK;
    long long total;

    memset(res, 0, sizeof *res);
    memset(&st, 0, sizeof st);
    if (cfg->capacity < 1 || cfg->capacity > PC_MAX_CAPACITY) {
        set_error(err, errsz, "capacity must be between 1 and 1000");
        return RC_INVALID;
    }
    if (cfg->producers < 1 || cfg->producers > PC_MAX_THREADS || cfg->consumers < 1 || cfg->consumers > PC_MAX_THREADS) {
        set_error(err, errsz, "producers and consumers must each be between 1 and 16");
        return RC_INVALID;
    }
    if (cfg->items_per_producer < 1 || cfg->items_per_producer > PC_MAX_ITEMS_PER_PRODUCER) {
        set_error(err, errsz, "items per producer must be between 1 and 100000");
        return RC_INVALID;
    }
    total = (long long)cfg->producers * cfg->items_per_producer;
    if (total > PC_MAX_TOTAL_ITEMS) {
        set_error(err, errsz, "total items must be at most 200000");
        return RC_INVALID;
    }

    res->producers = cfg->producers;
    res->consumers = cfg->consumers;
    res->per_producer = calloc((size_t)cfg->producers, sizeof(long long));
    res->per_consumer = calloc((size_t)cfg->consumers, sizeof(long long));
    st.buf = calloc((size_t)cfg->capacity, sizeof(long long));
    st.seen = calloc((size_t)total, 1);
    st.last_seq = malloc((size_t)cfg->producers * sizeof(long long));
    pw = calloc((size_t)cfg->producers, sizeof *pw);
    cw = calloc((size_t)cfg->consumers, sizeof *cw);
    pthreads = calloc((size_t)cfg->producers, sizeof *pthreads);
    cthreads = calloc((size_t)cfg->consumers, sizeof *cthreads);
    if (!res->per_producer || !res->per_consumer || !st.buf || !st.seen || !st.last_seq || !pw || !cw || !pthreads ||
        !cthreads) {
        set_error(err, errsz, "out of memory");
        rc = RC_INTERNAL;
        goto cleanup_alloc;
    }
    for (i = 0; i < cfg->producers; i++) st.last_seq[i] = -1;

    st.capacity = cfg->capacity;
    st.items_per_producer = cfg->items_per_producer;
    st.total = total;
    st.res = res;
    ebms_mutex_init(&st.mu);
    ebms_cond_init(&st.not_full);
    ebms_cond_init(&st.not_empty);

    for (i = 0; i < cfg->consumers; i++) {
        cw[i].st = &st;
        cw[i].id = i;
        if (ebms_thread_create(&cthreads[i], consumer_main, &cw[i]) != 0) {
            rc = RC_INTERNAL;
            break;
        }
        created_c++;
    }
    for (i = 0; rc == RC_OK && i < cfg->producers; i++) {
        pw[i].st = &st;
        pw[i].id = i;
        if (ebms_thread_create(&pthreads[i], producer_main, &pw[i]) != 0) {
            rc = RC_INTERNAL;
            break;
        }
        created_p++;
    }

    if (rc != RC_OK) {
        /* partial failure: wake everyone and release the threads that did start */
        ebms_mutex_lock(&st.mu);
        st.aborted = 1;
        st.closed = 1;
        ebms_cond_broadcast(&st.not_full);
        ebms_cond_broadcast(&st.not_empty);
        ebms_mutex_unlock(&st.mu);
        for (i = 0; i < created_p; i++) ebms_thread_join(pthreads[i]), res->threads_joined++;
        for (i = 0; i < created_c; i++) ebms_thread_join(cthreads[i]), res->threads_joined++;
        set_error(err, errsz, "could not start threads");
    } else {
        for (i = 0; i < created_p; i++) {
            ebms_thread_join(pthreads[i]);
            res->threads_joined++;
        }
        ebms_mutex_lock(&st.mu);
        st.closed = 1;
        ebms_cond_broadcast(&st.not_empty);
        ebms_mutex_unlock(&st.mu);
        for (i = 0; i < created_c; i++) {
            ebms_thread_join(cthreads[i]);
            res->threads_joined++;
        }
        res->exactly_once = res->consumed == total && res->duplicates == 0 && res->unknown_items == 0 &&
                            res->checksum_produced == res->checksum_consumed && res->produced == total;
    }

    ebms_cond_destroy(&st.not_full);
    ebms_cond_destroy(&st.not_empty);
    ebms_mutex_destroy(&st.mu);

cleanup_alloc:
    free(st.buf);
    free(st.seen);
    free(st.last_seq);
    free(pw);
    free(cw);
    free(pthreads);
    free(cthreads);
    if (rc != RC_OK) pc_result_free(res);
    return rc;
}

int producer_consumer_run(const JsonValue *input, Buf *out, char *err, size_t errsz) {
    PCConfig cfg;
    PCResult res;
    long long v;
    int i, rc;

    if (json_int_field(input, "capacity", 1, PC_MAX_CAPACITY, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.capacity = (int)v;
    if (json_int_field(input, "producers", 1, PC_MAX_THREADS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.producers = (int)v;
    if (json_int_field(input, "consumers", 1, PC_MAX_THREADS, 0, 1, &v, err, errsz) != RC_OK) return RC_INVALID;
    cfg.consumers = (int)v;
    if (json_int_field(input, "items_per_producer", 1, PC_MAX_ITEMS_PER_PRODUCER, 0, 1, &v, err, errsz) != RC_OK)
        return RC_INVALID;
    cfg.items_per_producer = (int)v;

    rc = pc_simulate(&cfg, &res, err, errsz);
    if (rc != RC_OK) return rc;

    buf_printf(out,
        "{\"ok\":true,\"capacity\":%d,\"producers\":%d,\"consumers\":%d,\"items_per_producer\":%d,"
        "\"items_expected\":%lld,\"produced\":%lld,\"consumed\":%lld,\"max_occupancy\":%lld,"
        "\"producer_waits_full\":%lld,\"consumer_waits_empty\":%lld,\"duplicates\":%lld,\"unknown_items\":%lld,"
        "\"order_violations\":%lld,\"checksum_produced\":%lld,\"checksum_consumed\":%lld,"
        "\"exactly_once\":%s,\"threads_joined\":%d,\"terminated\":true,",
        cfg.capacity, cfg.producers, cfg.consumers, cfg.items_per_producer,
        (long long)cfg.producers * cfg.items_per_producer, res.produced, res.consumed, res.max_occupancy,
        res.producer_waits_full, res.consumer_waits_empty, res.duplicates, res.unknown_items, res.order_violations,
        res.checksum_produced, res.checksum_consumed, res.exactly_once ? "true" : "false", res.threads_joined);
    buf_puts(out, "\"per_producer\":[");
    for (i = 0; i < cfg.producers; i++) buf_printf(out, "%s%lld", i ? "," : "", res.per_producer[i]);
    buf_puts(out, "],\"per_consumer\":[");
    for (i = 0; i < cfg.consumers; i++) buf_printf(out, "%s%lld", i ? "," : "", res.per_consumer[i]);
    buf_puts(out, "],\"synchronization\":");
    buf_json_string(out, "one mutex guards the buffer; not_full and not_empty condition variables; "
                         "closed flag set only after all producers are joined");
    buf_puts(out, "}");
    pc_result_free(&res);
    return out->oom ? RC_INTERNAL : RC_OK;
}

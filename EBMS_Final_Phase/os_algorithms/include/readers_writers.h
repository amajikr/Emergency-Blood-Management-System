/* Readers-writers lock with WRITER PREFERENCE, plus a threaded simulation that checks its invariants. */
#ifndef EBMS_READERS_WRITERS_H
#define EBMS_READERS_WRITERS_H

#include "common.h"
#include "json.h"
#include "ebms_sync.h"

#define RW_MAX_THREADS 16
#define RW_MAX_OPS 1000
#define RW_MAX_HOLD_MS 50
#define RW_MAX_SERIAL_MS 3000 /* upper bound on (total operations x hold time) */

typedef struct {
    ebms_mutex_t mu;
    ebms_cond_t can_read;
    ebms_cond_t can_write;
    int readers_active;
    int writer_active;
    int writers_waiting;
    long long max_readers;
    long long violations;
    long long version;
    long long a, b; /* writer updates a then b while holding the write lock; readers expect a == b */
    long long reads_done;
    long long writes_done;
} RWState;

typedef struct {
    int readers;
    int writers;
    int reads_per_reader;
    int writes_per_writer;
    int hold_ms;
} RWConfig;

typedef struct {
    long long reads_done;
    long long writes_done;
    long long max_readers;
    long long violations;
    long long torn_reads;
    long long final_version;
    int threads_joined;
} RWResult;

void rw_init(RWState *s);
void rw_destroy(RWState *s);
void rw_read_lock(RWState *s);
void rw_read_unlock(RWState *s);
void rw_write_lock(RWState *s);
void rw_write_unlock(RWState *s);

int rw_simulate(const RWConfig *cfg, RWResult *res, char *err, size_t errsz);
int readers_writers_run(const JsonValue *input, Buf *out, char *err, size_t errsz);

#endif

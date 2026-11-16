#include "ctest.h"
#include "readers_writers.h"

static RWState s;
static ebms_mutex_t logmu;
static int log_len = 0;
static char log_names[8][8];
static int readers_inside = 0, max_inside = 0;
static int reader2_entered = 0;

static void log_event(const char *name) {
    ebms_mutex_lock(&logmu);
    if (log_len < 8) snprintf(log_names[log_len], sizeof log_names[0], "%s", name);
    log_len++;
    ebms_mutex_unlock(&logmu);
}

static int waiting_writers(void) {
    int n;
    ebms_mutex_lock(&s.mu);
    n = s.writers_waiting;
    ebms_mutex_unlock(&s.mu);
    return n;
}

/* Holds the read lock until all three readers are inside together (only possible if readers overlap). */
static void overlap_reader(void *arg) {
    int waited = 0;
    (void)arg;
    rw_read_lock(&s);
    ebms_mutex_lock(&logmu);
    readers_inside++;
    if (readers_inside > max_inside) max_inside = readers_inside;
    ebms_mutex_unlock(&logmu);
    while (waited < 3000) {
        int now;
        ebms_mutex_lock(&logmu);
        now = readers_inside;
        ebms_mutex_unlock(&logmu);
        if (now >= 3) break;
        ebms_sleep_ms(1);
        waited++;
    }
    rw_read_unlock(&s);
    ebms_mutex_lock(&logmu);
    readers_inside--;
    ebms_mutex_unlock(&logmu);
}

static void writer_once(void *arg) {
    (void)arg;
    rw_write_lock(&s);
    log_event("W");
    rw_write_unlock(&s);
}

static void reader_once(void *arg) {
    (void)arg;
    rw_read_lock(&s);
    reader2_entered = 1;
    log_event("R2");
    rw_read_unlock(&s);
}

static void reader_probe(void *arg) {
    (void)arg;
    rw_read_lock(&s);
    log_event("R3");
    rw_read_unlock(&s);
}

int main(void) {
    RWConfig cfg;
    RWResult res;
    char err[EBMS_ERR_LEN];
    ebms_thread_t t[4];
    int i;

    /* 1. Readers overlap: three readers hold the lock at the same time */
    rw_init(&s);
    ebms_mutex_init(&logmu);
    for (i = 0; i < 3; i++) CHECK_EQ(ebms_thread_create(&t[i], overlap_reader, NULL), 0);
    for (i = 0; i < 3; i++) ebms_thread_join(t[i]);
    CHECK_EQ(max_inside, 3);
    CHECK_EQ(s.violations, 0);
    rw_destroy(&s);

    /* 2. Writer exclusion: a reader cannot enter while the writer holds the lock */
    rw_init(&s);
    reader2_entered = 0;
    {
        ebms_thread_t tw, tr;
        rw_write_lock(&s);
        CHECK_EQ(ebms_thread_create(&tr, reader_once, NULL), 0);
        ebms_sleep_ms(100);
        CHECK(reader2_entered == 0);
        rw_write_unlock(&s);
        ebms_thread_join(tr);
        CHECK(reader2_entered == 1);
        (void)tw;
    }
    rw_destroy(&s);

    /* 3. Writer preference: R1 holds the lock, W queues, then R3 must wait behind W */
    rw_init(&s);
    log_len = 0;
    ebms_mutex_init(&logmu);
    {
        ebms_thread_t tw, tr;
        int waited = 0;
        rw_read_lock(&s);
        CHECK_EQ(ebms_thread_create(&tw, writer_once, NULL), 0);
        while (waiting_writers() == 0 && waited < 3000) { ebms_sleep_ms(1); waited++; }
        CHECK_EQ(waiting_writers(), 1);
        CHECK_EQ(ebms_thread_create(&tr, reader_probe, NULL), 0);
        ebms_sleep_ms(100);
        CHECK_EQ(log_len, 0);             /* R3 is blocked even though only a reader holds the lock */
        rw_read_unlock(&s);               /* last reader leaves: writer goes first */
        ebms_thread_join(tw);
        ebms_thread_join(tr);
        CHECK_EQ(log_len, 2);
        CHECK(strcmp(log_names[0], "W") == 0);
        CHECK(strcmp(log_names[1], "R3") == 0);
    }
    rw_destroy(&s);

    /* 4. Full simulation: invariants hold and every thread finishes */
    cfg.readers = 4; cfg.writers = 2; cfg.reads_per_reader = 50; cfg.writes_per_writer = 20; cfg.hold_ms = 0;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_OK);
    CHECK_EQ(res.reads_done, 200);
    CHECK_EQ(res.writes_done, 40);
    CHECK_EQ(res.violations, 0);
    CHECK_EQ(res.torn_reads, 0);
    CHECK_EQ(res.final_version, 40);
    CHECK_EQ(res.threads_joined, 6);

    /* 5. With a hold time readers really overlap in the simulation */
    cfg.readers = 3; cfg.writers = 1; cfg.reads_per_reader = 10; cfg.writes_per_writer = 5; cfg.hold_ms = 5;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_OK);
    CHECK(res.max_readers >= 2);
    CHECK_EQ(res.violations, 0);
    CHECK_EQ(res.torn_reads, 0);

    /* 6. Invalid configurations */
    cfg.readers = 0; cfg.writers = 0; cfg.reads_per_reader = 1; cfg.writes_per_writer = 1; cfg.hold_ms = 0;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_INVALID);
    cfg.readers = 17; cfg.writers = 1;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_INVALID);
    cfg.readers = 2; cfg.writers = 1; cfg.hold_ms = 51;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_INVALID);
    cfg.hold_ms = 50; cfg.reads_per_reader = 1000; cfg.writes_per_writer = 1000;
    CHECK_EQ(rw_simulate(&cfg, &res, err, sizeof err), RC_INVALID); /* serial bound exceeded */

    CT_SUMMARY("test_readers_writers");
}

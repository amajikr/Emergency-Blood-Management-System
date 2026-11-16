#include "ctest.h"
#include "producer_consumer.h"

static int sim(int cap, int prod, int cons, int items, PCResult *r) {
    PCConfig cfg;
    char err[EBMS_ERR_LEN];
    cfg.capacity = cap;
    cfg.producers = prod;
    cfg.consumers = cons;
    cfg.items_per_producer = items;
    return pc_simulate(&cfg, r, err, sizeof err);
}

int main(void) {
    PCResult r;
    int k;

    /* Single slot, one producer and one consumer: never more than one item in the buffer */
    CHECK_EQ(sim(1, 1, 1, 500, &r), RC_OK);
    CHECK_EQ(r.produced, 500);
    CHECK_EQ(r.consumed, 500);
    CHECK_EQ(r.max_occupancy, 1);
    CHECK_EQ(r.exactly_once, 1);
    CHECK_EQ(r.threads_joined, 2);
    pc_result_free(&r);

    /* Several producers and consumers: every item exactly once, checksums match, per-producer FIFO kept */
    CHECK_EQ(sim(4, 3, 2, 2000, &r), RC_OK);
    CHECK_EQ(r.produced, 6000);
    CHECK_EQ(r.consumed, 6000);
    CHECK_EQ(r.duplicates, 0);
    CHECK_EQ(r.unknown_items, 0);
    CHECK_EQ(r.order_violations, 0);
    CHECK_EQ(r.checksum_produced, r.checksum_consumed);
    CHECK(r.max_occupancy <= 4);
    CHECK_EQ(r.exactly_once, 1);
    CHECK_EQ(r.per_producer[0], 2000);
    CHECK_EQ(r.per_producer[2], 2000);
    CHECK_EQ(r.threads_joined, 5);
    pc_result_free(&r);

    /* Repeat a tight, contended configuration to shake out races */
    for (k = 0; k < 20; k++) {
        CHECK_EQ(sim(2, 4, 4, 300, &r), RC_OK);
        CHECK_EQ(r.exactly_once, 1);
        CHECK_EQ(r.consumed, 1200);
        CHECK(r.max_occupancy <= 2);
        pc_result_free(&r);
    }

    /* Larger buffer, many consumers than producers */
    CHECK_EQ(sim(8, 2, 6, 5000, &r), RC_OK);
    CHECK_EQ(r.exactly_once, 1);
    pc_result_free(&r);

    /* Invalid configurations are rejected before any thread starts */
    CHECK_EQ(sim(0, 1, 1, 10, &r), RC_INVALID);
    CHECK_EQ(sim(1001, 1, 1, 10, &r), RC_INVALID);
    CHECK_EQ(sim(4, 0, 1, 10, &r), RC_INVALID);
    CHECK_EQ(sim(4, 17, 1, 10, &r), RC_INVALID);
    CHECK_EQ(sim(4, 1, 17, 10, &r), RC_INVALID);
    CHECK_EQ(sim(4, 1, 1, 0, &r), RC_INVALID);
    CHECK_EQ(sim(4, 16, 1, 20000, &r), RC_INVALID); /* 320000 total items */

    /* JSON entry point */
    {
        JsonValue *root = NULL;
        char err[EBMS_ERR_LEN];
        Buf out;
        int rc;
        buf_init(&out);
        rc = json_parse("{\"capacity\":3,\"producers\":2,\"consumers\":2,\"items_per_producer\":100}", 67, &root, err, sizeof err);
        CHECK_EQ(rc, RC_OK);
        rc = producer_consumer_run(root, &out, err, sizeof err);
        CHECK_EQ(rc, RC_OK);
        CHECK(strstr(out.data, "\"exactly_once\":true") != NULL);
        CHECK(strstr(out.data, "\"terminated\":true") != NULL);
        json_free(root);
        buf_free(&out);
    }

    CT_SUMMARY("test_producer_consumer");
}

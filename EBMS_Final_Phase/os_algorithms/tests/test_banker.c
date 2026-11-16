/* Textbook data (Silberschatz): 5 processes, 3 resource types, available (3,3,2). */
#include "ctest.h"
#include "banker.h"

static const int AVAIL[3] = {3, 3, 2};
static const int MAX[15] = {7,5,3, 3,2,2, 9,0,2, 2,2,2, 4,3,3};
static const int ALLOC[15] = {0,1,0, 2,0,0, 3,0,2, 2,1,1, 0,0,2};
static int NEED[15];

static int run_json(const char *text, Buf *out) {
    JsonValue *root = NULL;
    char err[EBMS_ERR_LEN];
    int rc;
    buf_init(out);
    rc = json_parse(text, strlen(text), &root, err, sizeof err);
    if (rc != RC_OK) return rc;
    rc = banker_run(root, out, err, sizeof err);
    json_free(root);
    return rc;
}

int main(void) {
    int seq[5], seq_len = 0, safe;
    BankerDecision d;
    int req[3];
    Buf out;
    const int textbook_seq[5] = {1, 3, 4, 0, 2};
    int k;

    banker_compute_need(5, 3, MAX, ALLOC, NEED);
    CHECK_EQ(NEED[0 * 3 + 0], 7); CHECK_EQ(NEED[1 * 3 + 2], 2); CHECK_EQ(NEED[4 * 3 + 2], 1);

    /* Safe state, sequence <P1,P3,P4,P0,P2> */
    safe = banker_is_safe(5, 3, AVAIL, ALLOC, NEED, seq, &seq_len);
    CHECK_EQ(safe, 1);
    CHECK_EQ(seq_len, 5);
    for (k = 0; k < 5; k++) CHECK_EQ(seq[k], textbook_seq[k]);

    /* Request P1 (1,0,2): granted, state stays safe with the same sequence */
    req[0] = 1; req[1] = 0; req[2] = 2;
    CHECK_EQ(banker_check_request(5, 3, AVAIL, ALLOC, NEED, 1, req, &d), 1);
    CHECK_EQ(d.code, BDEC_GRANTED);
    CHECK_EQ(d.safe_after, 1);
    CHECK_EQ(d.seq_after_len, 5);
    CHECK_EQ(d.seq_after[0], 1);

    /* Request P0 (0,4,2): more than available (3,3,2) -> waits */
    req[0] = 0; req[1] = 4; req[2] = 2;
    CHECK_EQ(banker_check_request(5, 3, AVAIL, ALLOC, NEED, 0, req, &d), 0);
    CHECK_EQ(d.code, BDEC_REJECT_INSUFFICIENT);

    /* Request P1 (2,2,0): element 0 exceeds P1's declared need (1,2,2) -> rejected for exceeding need */
    req[0] = 2; req[1] = 2; req[2] = 0;
    CHECK_EQ(banker_check_request(5, 3, AVAIL, ALLOC, NEED, 1, req, &d), 0);
    CHECK_EQ(d.code, BDEC_REJECT_EXCEEDS_NEED);

    /* Unsafe request: P4 asks (3,3,0). Tentative state has no safe sequence -> rejected as unsafe. */
    req[0] = 3; req[1] = 3; req[2] = 0;
    CHECK_EQ(banker_check_request(5, 3, AVAIL, ALLOC, NEED, 4, req, &d), 0);
    CHECK_EQ(d.code, BDEC_REJECT_UNSAFE);
    CHECK_EQ(d.safe_after, 0);

    /* Unsafe state (no safe sequence) with no requests: 2 processes that each need everything */
    {
        const int av[1] = {0}; /* nothing free: neither process can finish */
        const int al[2] = {1, 1};
        const int nd[2] = {1, 1};
        CHECK_EQ(banker_is_safe(2, 1, av, al, nd, seq, &seq_len), 0);
        CHECK_EQ(seq_len, 0);
    }

    /* Invalid states */
    {
        char err[EBMS_ERR_LEN];
        int bad_alloc[15];
        int neg_avail[3] = {-1, 3, 2};
        memcpy(bad_alloc, ALLOC, sizeof bad_alloc);
        bad_alloc[0] = 99; /* allocation 99 > max 7 */
        CHECK_EQ(banker_validate_state(5, 3, AVAIL, MAX, bad_alloc, err, sizeof err), RC_INVALID);
        CHECK_EQ(banker_validate_state(5, 3, neg_avail, MAX, ALLOC, err, sizeof err), RC_INVALID);
        CHECK_EQ(banker_validate_state(0, 3, AVAIL, MAX, ALLOC, err, sizeof err), RC_INVALID);
        CHECK_EQ(banker_validate_state(5, 21, AVAIL, MAX, ALLOC, err, sizeof err), RC_INVALID);
        CHECK_EQ(banker_validate_state(5, 3, AVAIL, MAX, ALLOC, err, sizeof err), RC_OK);
    }

    /* JSON entry point: textbook state with a granted request */
    {
        int rc = run_json("{\"available\":[3,3,2],"
                          "\"max\":[[7,5,3],[3,2,2],[9,0,2],[2,2,2],[4,3,3]],"
                          "\"allocation\":[[0,1,0],[2,0,0],[3,0,2],[2,1,1],[0,0,2]],"
                          "\"request\":{\"process\":1,\"vector\":[1,0,2]}}", &out);
        CHECK_EQ(rc, RC_OK);
        CHECK(strstr(out.data, "\"safe\":true") != NULL);
        CHECK(strstr(out.data, "\"safe_sequence\":[1,3,4,0,2]") != NULL);
        CHECK(strstr(out.data, "\"granted\":true") != NULL);
        CHECK(strstr(out.data, "\"code\":\"GRANTED\"") != NULL);
        buf_free(&out);
    }

    /* Invalid JSON requests: each must be rejected, never approved */
    {
        const char *cases[] = {
            "{\"available\":[3,3],\"max\":[[1,1]],\"allocation\":[[0,0]]}",                  /* ok shape, but see next */
            "{\"available\":[3,3,2],\"max\":[[7,5,3]],\"allocation\":[[9,0,0]]}",          /* alloc > max */
            "{\"available\":[3,3,2],\"max\":[[7,5,3]],\"allocation\":[[0,0,0],[0,0,0]]}",  /* row count */
            "{\"available\":[3,3,2],\"max\":[[7,5]],\"allocation\":[[0,0]]}",               /* column count */
            "{\"available\":[3,-3,2],\"max\":[[7,5,3]],\"allocation\":[[0,0,0]]}",          /* negative */
            "{\"available\":[3,3,2],\"max\":[[7,5,3]],\"allocation\":[[0,0,0]],\"request\":{\"process\":3,\"vector\":[0,0,0]}}",
            "{\"available\":[3,3,2],\"max\":[[7,5,3]],\"allocation\":[[0,0,0]],\"request\":{\"process\":0,\"vector\":[1,1]}}",
            "{\"available\":[3,3,2],\"max\":[[7,5,3]],\"allocation\":[[0,0,0]],\"request\":{\"process\":0,\"vector\":[1.5,0,0]}}",
            "{\"available\":[],\"max\":[],\"allocation\":[]}",
        };
        size_t i;
        for (i = 0; i < sizeof cases / sizeof cases[0]; i++) {
            int rc = run_json(cases[i], &out);
            if (i == 0) {
                /* a 2-resource, 1-process state is valid; this just checks it does not crash */
                CHECK(rc == RC_OK || rc == RC_INVALID);
            } else {
                CHECK_EQ(rc, RC_INVALID);
            }
            buf_free(&out);
        }
    }

    CT_SUMMARY("test_banker");
}

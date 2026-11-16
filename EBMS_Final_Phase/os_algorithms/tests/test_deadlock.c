#include "ctest.h"
#include "deadlock.h"

static int run_json(const char *text, Buf *out) {
    JsonValue *root = NULL;
    char err[EBMS_ERR_LEN];
    int rc;
    buf_init(out);
    rc = json_parse(text, strlen(text), &root, err, sizeof err);
    if (rc != RC_OK) return rc;
    rc = deadlock_run(root, out, err, sizeof err);
    json_free(root);
    return rc;
}

int main(void) {
    unsigned char adj[9];
    int cycle[9], cycle_len = 0, held[3] = {3, 1, 2};
    Buf out;
    int rc;

    /* Acyclic: P0 -> P1 -> P2 */
    memset(adj, 0, sizeof adj);
    adj[0 * 3 + 1] = 1;
    adj[1 * 3 + 2] = 1;
    CHECK_EQ(deadlock_find_cycle(3, adj, cycle, &cycle_len), 0);

    /* Three-cycle: P0 -> P1 -> P2 -> P0, victim is the cheapest member (P1 holds 1) */
    adj[2 * 3 + 0] = 1;
    CHECK_EQ(deadlock_find_cycle(3, adj, cycle, &cycle_len), 1);
    CHECK_EQ(cycle_len, 3);
    CHECK_EQ(deadlock_choose_victim(cycle_len, cycle, held), 1);

    /* Ties on held units go to the lower index */
    {
        const int h2[3] = {2, 2, 2};
        CHECK_EQ(deadlock_choose_victim(3, cycle, h2), 0);
    }

    /* Self-loop: P2 waits for itself */
    memset(adj, 0, sizeof adj);
    adj[2 * 3 + 2] = 1;
    CHECK_EQ(deadlock_find_cycle(3, adj, cycle, &cycle_len), 1);
    CHECK_EQ(cycle_len, 1);
    CHECK_EQ(cycle[0], 2);

    /* Two disjoint components, only one cyclic */
    memset(adj, 0, sizeof adj);
    adj[0 * 3 + 1] = 1;
    adj[1 * 3 + 0] = 1;
    CHECK_EQ(deadlock_find_cycle(3, adj, cycle, &cycle_len), 1);
    CHECK_EQ(cycle_len, 2);

    /* JSON: acyclic graph reports no deadlock */
    rc = run_json("{\"processes\":[\"P1\",\"P2\",\"P3\"],\"edges\":[{\"from\":\"P1\",\"to\":\"P2\"},{\"from\":\"P2\",\"to\":\"P3\"}]}", &out);
    CHECK_EQ(rc, RC_OK);
    CHECK(strstr(out.data, "\"cycle_found\":false") != NULL);
    CHECK(strstr(out.data, "\"deadlock\":false") != NULL);
    CHECK(strstr(out.data, "\"victim\":null") != NULL);
    buf_free(&out);

    /* JSON: cycle with held units picks the cheapest victim */
    rc = run_json("{\"processes\":[\"P1\",\"P2\",\"P3\"],\"edges\":[{\"from\":\"P1\",\"to\":\"P2\"},{\"from\":\"P2\",\"to\":\"P3\"},{\"from\":\"P3\",\"to\":\"P1\"}],"
                  "\"held\":{\"P1\":3,\"P2\":1,\"P3\":2}}", &out);
    CHECK_EQ(rc, RC_OK);
    CHECK(strstr(out.data, "\"deadlock\":true") != NULL);
    CHECK(strstr(out.data, "\"cycle\":[\"P1\",\"P2\",\"P3\"]") != NULL);
    CHECK(strstr(out.data, "\"victim\":\"P2\"") != NULL);
    CHECK(strstr(out.data, "\"victim_cost\":1") != NULL);
    buf_free(&out);

    /* JSON: multi-instance cycle is reported but not confirmed as deadlock */
    rc = run_json("{\"resource_model\":\"multi_instance\",\"processes\":[\"A\",\"B\"],\"edges\":[{\"from\":\"A\",\"to\":\"B\"},{\"from\":\"B\",\"to\":\"A\"}]}", &out);
    CHECK_EQ(rc, RC_OK);
    CHECK(strstr(out.data, "\"cycle_found\":true") != NULL);
    CHECK(strstr(out.data, "\"deadlock\":false") != NULL);
    buf_free(&out);

    /* JSON: self-loop */
    rc = run_json("{\"processes\":[\"P1\"],\"edges\":[{\"from\":\"P1\",\"to\":\"P1\"}]}", &out);
    CHECK_EQ(rc, RC_OK);
    CHECK(strstr(out.data, "\"cycle\":[\"P1\"]") != NULL);
    buf_free(&out);

    /* Invalid graphs */
    {
        const char *bad[] = {
            "{\"processes\":[]}",
            "{\"processes\":[\"P1\",\"P1\"]}",
            "{\"processes\":[\"P1\"],\"edges\":[{\"from\":\"P1\",\"to\":\"P9\"}]}",
            "{\"processes\":[\"P1\",\"P2\"],\"edges\":[{\"from\":\"P1\",\"to\":\"P2\"},{\"from\":\"P1\",\"to\":\"P2\"}]}",
            "{\"processes\":[\"P1\",\"P2\"],\"edges\":[{\"from\":\"P1\"}]}",
            "{\"processes\":[\"P1\",\"P2\"],\"edges\":\"nope\"}",
            "{\"processes\":[\"P1\"],\"resource_model\":\"banana\"}",
            "{\"processes\":[\"P1\"],\"held\":{\"P1\":-1}}",
            "{\"processes\":[\"P1\"],\"held\":{\"P7\":1}}",
            "{\"processes\":[\"bad name\"]}",
        };
        size_t i;
        for (i = 0; i < sizeof bad / sizeof bad[0]; i++) {
            rc = run_json(bad[i], &out);
            CHECK_EQ(rc, RC_INVALID);
            buf_free(&out);
        }
    }

    CT_SUMMARY("test_deadlock");
}

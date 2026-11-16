/* Expected values below were worked out by hand from the textbook traces in the comments. */
#include "ctest.h"
#include "scheduler.h"

static void mk(SchedProcess *p, const char *pid, int arrival, int burst, int priority) {
    memset(p, 0, sizeof *p);
    snprintf(p->pid, sizeof p->pid, "%s", pid);
    p->arrival = arrival;
    p->burst = burst;
    p->priority = priority;
}

static int run_sim(SchedAlgo a, int q, SchedProcess *ps, size_t n, SchedResult *r) {
    char err[EBMS_ERR_LEN];
    return scheduler_simulate(a, q, ps, n, r, err, sizeof err);
}

/* Parses and runs a scheduler request. Returns the return code; out holds the JSON on success. */
static int run_json(const char *module_json, Buf *out) {
    JsonValue *root = NULL;
    char err[EBMS_ERR_LEN];
    int rc;
    buf_init(out);
    rc = json_parse(module_json, strlen(module_json), &root, err, sizeof err);
    if (rc != RC_OK) return rc;
    rc = scheduler_run(root, out, err, sizeof err);
    json_free(root);
    return rc;
}

int main(void) {
    SchedProcess ps[8];
    SchedResult r;
    Buf out;
    int rc;
    (void)rc;

    /* FCFS textbook: P1(0,5) P2(1,3) P3(2,8) P4(3,6). CT 5,8,16,22; WT 0,4,6,13; avg WT 5.75; avg TAT 11.25 */
    mk(&ps[0], "P1", 0, 5, 0); mk(&ps[1], "P2", 1, 3, 0); mk(&ps[2], "P3", 2, 8, 0); mk(&ps[3], "P4", 3, 6, 0);
    CHECK_EQ(run_sim(ALG_FCFS, 0, ps, 4, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 5);  CHECK_EQ(r.procs[1].completion, 8);
    CHECK_EQ(r.procs[2].completion, 16); CHECK_EQ(r.procs[3].completion, 22);
    CHECK_EQ(r.procs[1].turnaround, 7);  CHECK_EQ(r.procs[3].turnaround, 19);
    CHECK_EQ(r.procs[1].waiting, 4);     CHECK_EQ(r.procs[2].waiting, 6);  CHECK_EQ(r.procs[3].waiting, 13);
    CHECK_EQ(r.procs[2].response, 6);    /* non-preemptive: RT == WT */
    CHECK_EQ(r.total_time, 22);
    scheduler_result_free(&r);

    /* SRTF textbook: P1(0,8) P2(1,4) P3(2,9) P4(3,5).
       Gantt P1 0-1, P2 1-5, P4 5-10, P1 10-17, P3 17-26. CT 17,5,26,10. WT 9,0,15,2 (avg 6.5). RT 0,0,15,2 */
    mk(&ps[0], "P1", 0, 8, 0); mk(&ps[1], "P2", 1, 4, 0); mk(&ps[2], "P3", 2, 9, 0); mk(&ps[3], "P4", 3, 5, 0);
    CHECK_EQ(run_sim(ALG_SRTF, 0, ps, 4, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 17); CHECK_EQ(r.procs[1].completion, 5);
    CHECK_EQ(r.procs[2].completion, 26); CHECK_EQ(r.procs[3].completion, 10);
    CHECK_EQ(r.procs[0].waiting, 9);     CHECK_EQ(r.procs[1].waiting, 0);
    CHECK_EQ(r.procs[2].waiting, 15);    CHECK_EQ(r.procs[3].waiting, 2);
    CHECK_EQ(r.procs[2].response, 15);   CHECK_EQ(r.procs[3].response, 2);
    CHECK_EQ(r.gantt_len, 5);
    CHECK_EQ(r.gantt[1].pid, 1); CHECK_EQ(r.gantt[1].start, 1); CHECK_EQ(r.gantt[1].end, 5);
    CHECK_EQ(r.gantt[4].pid, 2); CHECK_EQ(r.gantt[4].start, 17); CHECK_EQ(r.gantt[4].end, 26);
    scheduler_result_free(&r);

    /* Priority NP: P1(0,4,p2) P2(1,3,p1) P3(2,1,p4) P4(3,2,p3). P1 runs to completion first.
       Order P1, P2, P4, P3. CT 4,7,10,9 */
    mk(&ps[0], "P1", 0, 4, 2); mk(&ps[1], "P2", 1, 3, 1); mk(&ps[2], "P3", 2, 1, 4); mk(&ps[3], "P4", 3, 2, 3);
    CHECK_EQ(run_sim(ALG_PRIORITY_NP, 0, ps, 4, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 4); CHECK_EQ(r.procs[1].completion, 7);
    CHECK_EQ(r.procs[2].completion, 10); CHECK_EQ(r.procs[3].completion, 9);
    CHECK_EQ(r.procs[1].waiting, 3);    CHECK_EQ(r.procs[2].waiting, 7);
    scheduler_result_free(&r);

    /* Priority preemptive, same processes: P2 preempts P1 at t=1. Gantt P1 0-1, P2 1-4, P1 4-7, P4 7-9, P3 9-10.
       CT 7,4,10,9. WT 3,0,7,4. */
    mk(&ps[0], "P1", 0, 4, 2); mk(&ps[1], "P2", 1, 3, 1); mk(&ps[2], "P3", 2, 1, 4); mk(&ps[3], "P4", 3, 2, 3);
    CHECK_EQ(run_sim(ALG_PRIORITY_P, 0, ps, 4, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 7); CHECK_EQ(r.procs[1].completion, 4);
    CHECK_EQ(r.procs[2].completion, 10); CHECK_EQ(r.procs[3].completion, 9);
    CHECK_EQ(r.procs[0].waiting, 3);    CHECK_EQ(r.procs[0].response, 0);
    CHECK_EQ(r.gantt_len, 5);
    CHECK_EQ(r.gantt[2].pid, 0); CHECK_EQ(r.gantt[2].start, 4); CHECK_EQ(r.gantt[2].end, 7);
    scheduler_result_free(&r);

    /* Round Robin q=2, textbook: P1(0,5) P2(1,3) P3(2,1) P4(3,2).
       Gantt P1 0-2, P2 2-4, P3 4-5, P1 5-7, P4 7-9, P2 9-10, P1 10-11. CT 11,10,5,9. WT 6,6,2,4 (avg 4.5). RT 0,1,2,4 */
    mk(&ps[0], "P1", 0, 5, 0); mk(&ps[1], "P2", 1, 3, 0); mk(&ps[2], "P3", 2, 1, 0); mk(&ps[3], "P4", 3, 2, 0);
    CHECK_EQ(run_sim(ALG_RR, 2, ps, 4, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 11); CHECK_EQ(r.procs[1].completion, 10);
    CHECK_EQ(r.procs[2].completion, 5);  CHECK_EQ(r.procs[3].completion, 9);
    CHECK_EQ(r.procs[0].waiting, 6);     CHECK_EQ(r.procs[1].waiting, 6);
    CHECK_EQ(r.procs[2].waiting, 2);     CHECK_EQ(r.procs[3].waiting, 4);
    CHECK_EQ(r.procs[1].response, 1);    CHECK_EQ(r.procs[3].response, 4);
    CHECK_EQ(r.gantt_len, 7);
    CHECK_EQ(r.gantt[5].pid, 1); CHECK_EQ(r.gantt[5].start, 9);  CHECK_EQ(r.gantt[5].end, 10);
    CHECK_EQ(r.gantt[6].pid, 0); CHECK_EQ(r.gantt[6].start, 10); CHECK_EQ(r.gantt[6].end, 11);
    scheduler_result_free(&r);

    /* Idle CPU: P1(3,2) FCFS. IDLE 0-3, then P1 3-5. */
    mk(&ps[0], "P1", 3, 2, 0);
    CHECK_EQ(run_sim(ALG_FCFS, 0, ps, 1, &r), RC_OK);
    CHECK_EQ(r.gantt_len, 2);
    CHECK_EQ(r.gantt[0].pid, SCHED_IDLE); CHECK_EQ(r.gantt[0].end, 3);
    CHECK_EQ(r.procs[0].response, 0);     CHECK_EQ(r.procs[0].first_start, 3);
    CHECK_EQ(r.total_time, 5);
    scheduler_result_free(&r);

    /* Simultaneous arrivals: input order breaks the tie. P1(0,2) P2(0,1) under FCFS: P1 then P2. */
    mk(&ps[0], "P1", 0, 2, 0); mk(&ps[1], "P2", 0, 1, 0);
    CHECK_EQ(run_sim(ALG_FCFS, 0, ps, 2, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 2); CHECK_EQ(r.procs[1].completion, 3);
    CHECK_EQ(r.procs[1].waiting, 2);
    scheduler_result_free(&r);

    /* SRTF tie: equal remaining time, earlier arrival wins; no needless preemption. */
    mk(&ps[0], "P1", 0, 3, 0); mk(&ps[1], "P2", 1, 2, 0);
    CHECK_EQ(run_sim(ALG_SRTF, 0, ps, 2, &r), RC_OK);
    CHECK_EQ(r.procs[0].completion, 3); CHECK_EQ(r.procs[1].completion, 5);
    scheduler_result_free(&r);

    /* Invalid inputs through the JSON entry point */
    {
        const char *cases[] = {
            "{\"algorithm\":\"RR\",\"processes\":[{\"pid\":\"P1\",\"burst\":2}]}",                 /* no quantum */
            "{\"algorithm\":\"RR\",\"quantum\":0,\"processes\":[{\"pid\":\"P1\",\"burst\":2}]}",    /* quantum 0 */
            "{\"algorithm\":\"FOO\",\"processes\":[{\"pid\":\"P1\",\"burst\":2}]}",                /* unknown alg */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":0}]}",               /* burst 0 */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":2},{\"pid\":\"P1\",\"burst\":2}]}", /* dup */
            "{\"algorithm\":\"FCFS\",\"processes\":[]}",                                           /* empty */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"bad name\",\"burst\":2}]}",         /* pid chars */
            "{\"algorithm\":\"PRIORITY_NP\",\"processes\":[{\"pid\":\"P1\",\"burst\":2}]}",        /* priority missing */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":2,\"arrival\":-1}]}", /* negative arrival */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":5001}]}",            /* burst over limit */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":4000},{\"pid\":\"P2\",\"burst\":4000}]}", /* total */
            "{\"algorithm\":\"FCFS\",\"processes\":[{\"pid\":\"P1\",\"burst\":2.5}]}",            /* float burst */
        };
        size_t k;
        for (k = 0; k < sizeof cases / sizeof cases[0]; k++) {
            int code = run_json(cases[k], &out);
            CHECK_EQ(code, RC_INVALID);
            buf_free(&out);
        }
    }
    {
        Buf good;
        int code = run_json("{\"algorithm\":\"RR\",\"quantum\":2,\"processes\":[{\"pid\":\"P-1\",\"arrival\":0,\"burst\":5}]}",
                            &good);
        CHECK_EQ(code, RC_OK);
        CHECK(strstr(good.data, "\"averages\":{\"turnaround\":5.00") != NULL);
        CHECK(strstr(good.data, "\"gantt\":[{\"pid\":\"P-1\",\"start\":0,\"end\":5}]") != NULL);
        buf_free(&good);
    }

    CT_SUMMARY("test_scheduler");
}

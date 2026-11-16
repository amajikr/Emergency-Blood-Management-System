/*
 * Unit-time CPU scheduling simulation.
 * Time advances one tick at a time, which makes idle periods, preemption and quantum expiry exact.
 *
 * Tie-breaking (documented in README): every policy compares (primary key, arrival time, input order).
 *   FCFS: primary = 0 (so arrival decides).   SJF: burst.   SRTF: remaining time.
 *   Priority: priority value, lower number = higher priority.
 *   RR: FIFO ready queue. A process arriving on the same tick a quantum expires is queued before the
 *       preempted process.
 */
#include <string.h>
#include "scheduler.h"

static const char *ALGO_NAMES[ALG_COUNT] = {"FCFS", "SJF", "SRTF", "PRIORITY_NP", "PRIORITY_P", "RR"};

const char *sched_algo_name(SchedAlgo a) {
    return (a >= 0 && a < ALG_COUNT) ? ALGO_NAMES[a] : "UNKNOWN";
}

int sched_parse_algo(const char *name, SchedAlgo *out) {
    int i;
    if (!name) return -1;
    for (i = 0; i < ALG_COUNT; i++) {
        if (strcmp(name, ALGO_NAMES[i]) == 0) {
            *out = (SchedAlgo)i;
            return 0;
        }
    }
    return -1;
}

static int is_preemptive(SchedAlgo a) { return a == ALG_SRTF || a == ALG_PRIORITY_P; }
static int needs_priority(SchedAlgo a) { return a == ALG_PRIORITY_NP || a == ALG_PRIORITY_P; }

static int primary_key(SchedAlgo algo, const SchedProcess *ps, const int *rem, int i) {
    switch (algo) {
    case ALG_SJF: return ps[i].burst;
    case ALG_SRTF: return rem[i];
    case ALG_PRIORITY_NP:
    case ALG_PRIORITY_P: return ps[i].priority;
    default: return 0; /* FCFS */
    }
}

/* Returns 1 if process i should run before process j under the given policy. */
static int key_less(SchedAlgo algo, const SchedProcess *ps, const int *rem, int i, int j) {
    int pi = primary_key(algo, ps, rem, i);
    int pj = primary_key(algo, ps, rem, j);
    if (pi != pj) return pi < pj;
    if (ps[i].arrival != ps[j].arrival) return ps[i].arrival < ps[j].arrival;
    return i < j;
}

static int pick_best(SchedAlgo algo, const SchedProcess *ps, const int *rem, size_t n, int t) {
    int best = -1;
    size_t i;
    for (i = 0; i < n; i++) {
        if (rem[i] > 0 && ps[i].arrival <= t) {
            if (best < 0 || key_less(algo, ps, rem, (int)i, best)) best = (int)i;
        }
    }
    return best;
}

static int gantt_push(SchedResult *r, int pid, int start, int end) {
    GanttSegment *g;
    size_t nc;
    if (r->gantt_len > 0) {
        GanttSegment *last = &r->gantt[r->gantt_len - 1];
        if (last->pid == pid && last->end == start) {
            last->end = end;
            return RC_OK;
        }
    }
    if (r->gantt_len == r->gantt_cap) {
        nc = r->gantt_cap ? r->gantt_cap * 2 : 64;
        g = realloc(r->gantt, nc * sizeof *g);
        if (!g) return RC_INTERNAL;
        r->gantt = g;
        r->gantt_cap = nc;
    }
    r->gantt[r->gantt_len].pid = pid;
    r->gantt[r->gantt_len].start = start;
    r->gantt[r->gantt_len].end = end;
    r->gantt_len++;
    return RC_OK;
}

static void q_push(size_t *queue, size_t cap, size_t head, size_t *qlen, size_t value) {
    queue[(head + *qlen) % cap] = value;
    (*qlen)++;
}

void scheduler_result_free(SchedResult *res) {
    free(res->procs);
    free(res->gantt);
    memset(res, 0, sizeof *res);
}

int scheduler_simulate(SchedAlgo algo, int quantum, const SchedProcess *in, size_t n, SchedResult *res,
                       char *err, size_t errsz) {
    int *rem = NULL;
    size_t *queue = NULL;
    size_t i, head = 0, qlen = 0, done = 0;
    int running = -1, used = 0, t = 0, rc = RC_OK;

    memset(res, 0, sizeof *res);
    if (n == 0 || n > SCHED_MAX_PROCESSES) {
        set_error(err, errsz, "process count must be between 1 and 100");
        return RC_INVALID;
    }
    if (algo == ALG_RR && quantum < 1) {
        set_error(err, errsz, "quantum must be at least 1 for RR");
        return RC_INVALID;
    }
    res->procs = malloc(n * sizeof *res->procs);
    rem = malloc(n * sizeof *rem);
    queue = malloc(n * sizeof *queue);
    if (!res->procs || !rem || !queue) {
        set_error(err, errsz, "out of memory");
        rc = RC_INTERNAL;
        goto cleanup;
    }
    res->count = n;
    for (i = 0; i < n; i++) {
        res->procs[i] = in[i];
        res->procs[i].first_start = -1;
        res->procs[i].completion = -1;
        rem[i] = in[i].burst;
    }

    if (algo == ALG_RR) {
        for (i = 0; i < n; i++)
            if (res->procs[i].arrival == 0) q_push(queue, n, head, &qlen, i);
    }

    while (done < n) {
        if (algo == ALG_RR) {
            if (running < 0 && qlen > 0) {
                running = (int)queue[head];
                head = (head + 1) % n;
                qlen--;
                used = 0;
            }
        } else if (running < 0 || is_preemptive(algo)) {
            int best = pick_best(algo, res->procs, rem, n, t);
            if (best >= 0 && best != running) {
                running = best;
                used = 0;
            }
        }

        if (running < 0) {
            if (gantt_push(res, SCHED_IDLE, t, t + 1) != RC_OK) goto oom;
            t++;
        } else {
            if (res->procs[running].first_start < 0) res->procs[running].first_start = t;
            rem[running]--;
            used++;
            if (gantt_push(res, running, t, t + 1) != RC_OK) goto oom;
            t++;
            if (rem[running] == 0) {
                res->procs[running].completion = t;
                done++;
                running = -1;
            }
        }

        if (algo == ALG_RR) {
            /* new arrivals enter the queue first, then a preempted process at the same tick */
            for (i = 0; i < n; i++)
                if (res->procs[i].arrival == t) q_push(queue, n, head, &qlen, i);
            if (running >= 0 && used == quantum) {
                q_push(queue, n, head, &qlen, (size_t)running);
                running = -1;
            }
        }
    }

    res->total_time = t;
    for (i = 0; i < n; i++) {
        SchedProcess *p = &res->procs[i];
        p->turnaround = p->completion - p->arrival;
        p->waiting = p->turnaround - p->burst;
        p->response = p->first_start - p->arrival;
    }
    goto cleanup;

oom:
    set_error(err, errsz, "out of memory");
    rc = RC_INTERNAL;
cleanup:
    free(rem);
    free(queue);
    if (rc != RC_OK) scheduler_result_free(res);
    return rc;
}

static int valid_pid_text(const char *s) { return valid_name(s); }

int scheduler_run(const JsonValue *input, Buf *out, char *err, size_t errsz) {
    const char *alg_name = NULL;
    SchedAlgo algo;
    long long quantum = 1;
    const JsonValue *plist;
    SchedProcess *ps = NULL;
    SchedResult res;
    size_t i, j, n;
    long long total_burst = 0;
    int rc = RC_INVALID;
    double sum_tat = 0, sum_wait = 0, sum_resp = 0;
    int idle_time = 0;
    char msg[EBMS_ERR_LEN];

    memset(&res, 0, sizeof res);
    if (json_string_field(input, "algorithm", &alg_name, 1, err, errsz) != RC_OK) return RC_INVALID;
    if (sched_parse_algo(alg_name, &algo) != 0) {
        set_error(err, errsz, "unknown algorithm: use FCFS, SJF, SRTF, PRIORITY_NP, PRIORITY_P or RR");
        return RC_INVALID;
    }
    if (json_int_field(input, "quantum", 1, SCHED_MAX_QUANTUM, 1, algo == ALG_RR, &quantum, err, errsz) != RC_OK)
        return RC_INVALID;

    plist = json_get(input, "processes");
    if (!plist || plist->type != JV_ARRAY) {
        set_error(err, errsz, "'processes' must be a list");
        return RC_INVALID;
    }
    n = plist->count;
    if (n < 1 || n > SCHED_MAX_PROCESSES) {
        set_error(err, errsz, "process count must be between 1 and 100");
        return RC_INVALID;
    }
    ps = calloc(n, sizeof *ps);
    if (!ps) {
        set_error(err, errsz, "out of memory");
        return RC_INTERNAL;
    }
    for (i = 0; i < n; i++) {
        const JsonValue *e = plist->items[i];
        const char *pid = NULL;
        long long arrival = 0, burst = 0, prio = 0;
        if (e->type != JV_OBJECT) {
            set_error(err, errsz, "each process must be an object");
            goto fail;
        }
        if (json_string_field(e, "pid", &pid, 1, err, errsz) != RC_OK) goto fail;
        if (!valid_pid_text(pid)) {
            set_error(err, errsz, "'pid' must be 1-32 letters, digits, '_' or '-'");
            goto fail;
        }
        for (j = 0; j < i; j++) {
            if (strcmp(ps[j].pid, pid) == 0) {
                snprintf(msg, sizeof msg, "duplicate pid '%s'", pid);
                set_error(err, errsz, msg);
                goto fail;
            }
        }
        snprintf(ps[i].pid, sizeof ps[i].pid, "%s", pid);
        if (json_int_field(e, "arrival", 0, SCHED_MAX_ARRIVAL, 0, 0, &arrival, err, errsz) != RC_OK) goto fail;
        if (json_int_field(e, "burst", 1, SCHED_MAX_BURST, 0, 1, &burst, err, errsz) != RC_OK) goto fail;
        if (json_int_field(e, "priority", -SCHED_MAX_PRIORITY, SCHED_MAX_PRIORITY, 0, needs_priority(algo), &prio,
                           err, errsz) != RC_OK)
            goto fail;
        ps[i].arrival = (int)arrival;
        ps[i].burst = (int)burst;
        ps[i].priority = (int)prio;
        total_burst += burst;
    }
    if (total_burst > SCHED_MAX_TOTAL_BURST) {
        set_error(err, errsz, "total burst time must be at most 5000 ticks");
        goto fail;
    }

    rc = scheduler_simulate(algo, (int)quantum, ps, n, &res, err, errsz);
    if (rc != RC_OK) goto fail;

    for (i = 0; i < n; i++) {
        sum_tat += res.procs[i].turnaround;
        sum_wait += res.procs[i].waiting;
        sum_resp += res.procs[i].response;
    }
    for (i = 0; i < res.gantt_len; i++)
        if (res.gantt[i].pid == SCHED_IDLE) idle_time += res.gantt[i].end - res.gantt[i].start;

    buf_puts(out, "{\"ok\":true,\"algorithm\":");
    buf_json_string(out, sched_algo_name(algo));
    if (algo == ALG_RR) buf_printf(out, ",\"quantum\":%lld", quantum);
    else buf_puts(out, ",\"quantum\":null");
    buf_puts(out, ",\"tie_breaking\":");
    buf_json_string(out,
        algo == ALG_RR ? "FIFO queue; arrivals on the same tick enter before a preempted process"
        : algo == ALG_FCFS ? "earliest arrival, then input order"
        : algo == ALG_SJF ? "shortest burst, then earliest arrival, then input order"
        : algo == ALG_SRTF ? "shortest remaining time, then earliest arrival, then input order"
        : "lowest priority number, then earliest arrival, then input order");
    buf_printf(out, ",\"total_time\":%d,\"idle_time\":%d", res.total_time, idle_time);

    buf_puts(out, ",\"processes\":[");
    for (i = 0; i < n; i++) {
        SchedProcess *p = &res.procs[i];
        if (i) buf_puts(out, ",");
        buf_puts(out, "{\"pid\":");
        buf_json_string(out, p->pid);
        buf_printf(out, ",\"arrival\":%d,\"burst\":%d,\"priority\":%d,\"first_start\":%d,\"completion\":%d,"
                        "\"turnaround\":%d,\"waiting\":%d,\"response\":%d}",
                   p->arrival, p->burst, p->priority, p->first_start, p->completion, p->turnaround, p->waiting,
                   p->response);
    }
    buf_puts(out, "],\"gantt\":[");
    for (i = 0; i < res.gantt_len; i++) {
        GanttSegment *g = &res.gantt[i];
        if (i) buf_puts(out, ",");
        if (g->pid == SCHED_IDLE) buf_puts(out, "{\"pid\":\"IDLE\"");
        else {
            buf_puts(out, "{\"pid\":");
            buf_json_string(out, res.procs[g->pid].pid);
        }
        buf_printf(out, ",\"start\":%d,\"end\":%d}", g->start, g->end);
    }
    buf_puts(out, "],\"idle_intervals\":[");
    {
        int first = 1;
        for (i = 0; i < res.gantt_len; i++) {
            if (res.gantt[i].pid != SCHED_IDLE) continue;
            buf_printf(out, "%s{\"start\":%d,\"end\":%d}", first ? "" : ",", res.gantt[i].start, res.gantt[i].end);
            first = 0;
        }
    }
    buf_printf(out, "],\"averages\":{\"turnaround\":%.2f,\"waiting\":%.2f,\"response\":%.2f}}",
               sum_tat / (double)n, sum_wait / (double)n, sum_resp / (double)n);

    free(ps);
    scheduler_result_free(&res);
    return RC_OK;

fail:
    free(ps);
    scheduler_result_free(&res);
    return rc == RC_OK ? RC_INVALID : rc;
}

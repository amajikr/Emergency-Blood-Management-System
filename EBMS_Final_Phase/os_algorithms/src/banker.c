/*
 * Banker's algorithm (Dijkstra). Academic resource-allocation simulation only.
 * It is NOT a blood-compatibility, inventory, or expiry rule: the DB layer enforces those.
 * An unsafe state does not prove deadlock; it only means the scheduler cannot guarantee completion.
 */
#include <string.h>
#include "banker.h"

const char *banker_code_name(BankerCode c) {
    switch (c) {
    case BDEC_GRANTED: return "GRANTED";
    case BDEC_REJECT_EXCEEDS_NEED: return "REJECTED_EXCEEDS_NEED";
    case BDEC_REJECT_INSUFFICIENT: return "REJECTED_INSUFFICIENT";
    case BDEC_REJECT_UNSAFE: return "REJECTED_UNSAFE";
    }
    return "UNKNOWN";
}

int banker_validate_state(int n, int m, const int *available, const int *max, const int *allocation,
                          char *err, size_t errsz) {
    int i, j;
    if (n < 1 || n > BANKER_MAX_PROCESSES) {
        set_error(err, errsz, "process count must be between 1 and 50");
        return RC_INVALID;
    }
    if (m < 1 || m > BANKER_MAX_RESOURCES) {
        set_error(err, errsz, "resource type count must be between 1 and 20");
        return RC_INVALID;
    }
    for (j = 0; j < m; j++) {
        if (available[j] < 0 || available[j] > BANKER_MAX_VALUE) {
            set_error(err, errsz, "available values must be integers from 0 to 10000");
            return RC_INVALID;
        }
    }
    for (i = 0; i < n; i++) {
        for (j = 0; j < m; j++) {
            int mx = max[i * m + j], al = allocation[i * m + j];
            if (mx < 0 || mx > BANKER_MAX_VALUE || al < 0 || al > BANKER_MAX_VALUE) {
                set_error(err, errsz, "matrix values must be integers from 0 to 10000");
                return RC_INVALID;
            }
            if (al > mx) {
                set_error(err, errsz, "invalid state: allocation exceeds maximum claim");
                return RC_INVALID;
            }
        }
    }
    return RC_OK;
}

void banker_compute_need(int n, int m, const int *max, const int *allocation, int *need) {
    int k;
    for (k = 0; k < n * m; k++) need[k] = max[k] - allocation[k];
}

int banker_is_safe(int n, int m, const int *available, const int *allocation, const int *need, int *seq,
                   int *seq_len) {
    int work[BANKER_MAX_RESOURCES];
    int finished[BANKER_MAX_PROCESSES];
    int i, j, count = 0, progress = 1;
    for (j = 0; j < m; j++) work[j] = available[j];
    for (i = 0; i < n; i++) finished[i] = 0;
    *seq_len = 0;
    while (progress) {
        progress = 0;
        for (i = 0; i < n; i++) {
            int can = !finished[i];
            for (j = 0; can && j < m; j++)
                if (need[i * m + j] > work[j]) can = 0;
            if (!can) continue;
            for (j = 0; j < m; j++) work[j] += allocation[i * m + j];
            finished[i] = 1;
            seq[(*seq_len)++] = i;
            count++;
            progress = 1;
        }
    }
    return count == n;
}

int banker_check_request(int n, int m, const int *available, const int *allocation, const int *need, int pid,
                         const int *request, BankerDecision *d) {
    int avail2[BANKER_MAX_RESOURCES];
    int alloc2[BANKER_MAX_PROCESSES * BANKER_MAX_RESOURCES];
    int need2[BANKER_MAX_PROCESSES * BANKER_MAX_RESOURCES];
    int j, k;
    memset(d, 0, sizeof *d);
    for (j = 0; j < m; j++) {
        if (request[j] > need[pid * m + j]) {
            d->code = BDEC_REJECT_EXCEEDS_NEED;
            return 0;
        }
    }
    for (j = 0; j < m; j++) {
        if (request[j] > available[j]) {
            d->code = BDEC_REJECT_INSUFFICIENT;
            return 0;
        }
    }
    /* Pretend to grant the request on copies, then test whether the resulting state is safe. */
    for (j = 0; j < m; j++) avail2[j] = available[j] - request[j];
    for (k = 0; k < n * m; k++) {
        alloc2[k] = allocation[k];
        need2[k] = need[k];
    }
    for (j = 0; j < m; j++) {
        alloc2[pid * m + j] += request[j];
        need2[pid * m + j] -= request[j];
    }
    d->safe_after = banker_is_safe(n, m, avail2, alloc2, need2, d->seq_after, &d->seq_after_len);
    if (!d->safe_after) {
        d->code = BDEC_REJECT_UNSAFE;
        d->seq_after_len = 0;
        return 0;
    }
    d->code = BDEC_GRANTED;
    return 1;
}

/* Reads a JSON array of exactly `len` integers (0..10000) into dst. */
static int read_vector(const JsonValue *arr, size_t len, int *dst, const char *name, char *err, size_t errsz) {
    size_t i;
    char msg[EBMS_ERR_LEN];
    if (!arr || arr->type != JV_ARRAY || arr->count != len) {
        snprintf(msg, sizeof msg, "'%s' must be a list of %zu integers", name, len);
        set_error(err, errsz, msg);
        return RC_INVALID;
    }
    for (i = 0; i < len; i++) {
        const JsonValue *e = arr->items[i];
        if (e->type != JV_INT || e->integer < 0 || e->integer > BANKER_MAX_VALUE) {
            snprintf(msg, sizeof msg, "'%s' values must be integers from 0 to 10000", name);
            set_error(err, errsz, msg);
            return RC_INVALID;
        }
        dst[i] = (int)e->integer;
    }
    return RC_OK;
}

static int read_matrix(const JsonValue *rows, size_t n, size_t m, int *dst, const char *name, char *err,
                       size_t errsz) {
    size_t i;
    char msg[EBMS_ERR_LEN];
    if (!rows || rows->type != JV_ARRAY || rows->count != n) {
        snprintf(msg, sizeof msg, "'%s' must have one row per process", name);
        set_error(err, errsz, msg);
        return RC_INVALID;
    }
    for (i = 0; i < n; i++) {
        if (read_vector(rows->items[i], m, dst + i * m, name, err, errsz) != RC_OK) return RC_INVALID;
    }
    return RC_OK;
}

int banker_run(const JsonValue *input, Buf *out, char *err, size_t errsz) {
    const JsonValue *avail_v = json_get(input, "available");
    const JsonValue *max_v = json_get(input, "max");
    const JsonValue *alloc_v = json_get(input, "allocation");
    const JsonValue *req_v = json_get(input, "request");
    int n, m, i, j;
    int avail[BANKER_MAX_RESOURCES];
    int maxm[BANKER_MAX_PROCESSES * BANKER_MAX_RESOURCES];
    int alloc[BANKER_MAX_PROCESSES * BANKER_MAX_RESOURCES];
    int need[BANKER_MAX_PROCESSES * BANKER_MAX_RESOURCES];
    int seq[BANKER_MAX_PROCESSES];
    int seq_len = 0, safe;
    int has_request = 0, req_pid = -1, req_vec[BANKER_MAX_RESOURCES];
    BankerDecision dec;

    if (!avail_v || avail_v->type != JV_ARRAY || avail_v->count < 1 || avail_v->count > BANKER_MAX_RESOURCES) {
        set_error(err, errsz, "'available' must be a list of 1-20 integers");
        return RC_INVALID;
    }
    m = (int)avail_v->count;
    if (!max_v || max_v->type != JV_ARRAY || max_v->count < 1 || max_v->count > BANKER_MAX_PROCESSES) {
        set_error(err, errsz, "'max' must have 1-50 process rows");
        return RC_INVALID;
    }
    n = (int)max_v->count;
    if (read_vector(avail_v, (size_t)m, avail, "available", err, errsz) != RC_OK) return RC_INVALID;
    if (read_matrix(max_v, (size_t)n, (size_t)m, maxm, "max", err, errsz) != RC_OK) return RC_INVALID;
    if (read_matrix(alloc_v, (size_t)n, (size_t)m, alloc, "allocation", err, errsz) != RC_OK) return RC_INVALID;
    if (banker_validate_state(n, m, avail, maxm, alloc, err, errsz) != RC_OK) return RC_INVALID;
    banker_compute_need(n, m, maxm, alloc, need);

    if (req_v && req_v->type != JV_NULL) {
        long long pid = -1;
        const JsonValue *vec;
        if (req_v->type != JV_OBJECT) {
            set_error(err, errsz, "'request' must be an object");
            return RC_INVALID;
        }
        if (json_int_field(req_v, "process", 0, n - 1, 0, 1, &pid, err, errsz) != RC_OK) return RC_INVALID;
        vec = json_get(req_v, "vector");
        if (read_vector(vec, (size_t)m, req_vec, "request.vector", err, errsz) != RC_OK) return RC_INVALID;
        has_request = 1;
        req_pid = (int)pid;
    }

    safe = banker_is_safe(n, m, avail, alloc, need, seq, &seq_len);

    buf_printf(out, "{\"ok\":true,\"processes\":%d,\"resources\":%d,\"safe\":%s,\"safe_sequence\":[", n, m,
               safe ? "true" : "false");
    for (i = 0; safe && i < seq_len; i++) buf_printf(out, "%s%d", i ? "," : "", seq[i]);
    buf_puts(out, "],\"need\":[");
    for (i = 0; i < n; i++) {
        buf_puts(out, i ? ",[" : "[");
        for (j = 0; j < m; j++) buf_printf(out, "%s%d", j ? "," : "", need[i * m + j]);
        buf_puts(out, "]");
    }
    buf_puts(out, "]");

    if (has_request) {
        int granted = banker_check_request(n, m, avail, alloc, need, req_pid, req_vec, &dec);
        buf_printf(out, ",\"request\":{\"process\":%d,\"granted\":%s,\"code\":\"%s\",\"reason\":",
                   req_pid, granted ? "true" : "false", banker_code_name(dec.code));
        switch (dec.code) {
        case BDEC_GRANTED: buf_json_string(out, "granted: state stays safe"); break;
        case BDEC_REJECT_EXCEEDS_NEED: buf_json_string(out, "request exceeds declared need"); break;
        case BDEC_REJECT_INSUFFICIENT: buf_json_string(out, "insufficient available"); break;
        case BDEC_REJECT_UNSAFE: buf_json_string(out, "unsafe Banker's state"); break;
        }
        buf_puts(out, ",\"safe_sequence_after\":[");
        for (i = 0; i < dec.seq_after_len; i++) buf_printf(out, "%s%d", i ? "," : "", dec.seq_after[i]);
        buf_puts(out, "]}");
    }
    buf_puts(out, "}");
    return out->oom ? RC_INTERNAL : RC_OK;
}

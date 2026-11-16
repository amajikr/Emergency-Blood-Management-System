/*
 * Wait-for graph deadlock detection.
 *
 * Resource model assumed by default ("single_instance"): every resource type has exactly one instance, so a
 * wait-for edge P -> Q means P waits for a resource held by Q. In that model a directed cycle is a deadlock.
 *
 * "multi_instance" is accepted but deliberately conservative: a cycle is necessary but NOT sufficient for
 * deadlock when resources have several instances, so the output reports cycle_found=true, deadlock=false.
 */
#include <string.h>
#include "deadlock.h"

static int dfs(int u, int n, const unsigned char *adj, int *color, int *stack, int *depth, int *cycle,
               int *cycle_len) {
    int v, k;
    color[u] = 1;
    stack[(*depth)++] = u;
    for (v = 0; v < n; v++) {
        if (!adj[u * n + v]) continue;
        if (color[v] == 1) { /* back edge: the cycle is the stack segment starting at v */
            for (k = 0; k < *depth; k++)
                if (stack[k] == v) break;
            *cycle_len = *depth - k;
            memcpy(cycle, stack + k, (size_t)(*cycle_len) * sizeof *cycle);
            return 1;
        }
        if (color[v] == 0 && dfs(v, n, adj, color, stack, depth, cycle, cycle_len)) return 1;
    }
    color[u] = 2;
    (*depth)--;
    return 0;
}

int deadlock_find_cycle(int n, const unsigned char *adj, int *cycle, int *cycle_len) {
    int color[DEADLOCK_MAX_PROCESSES] = {0};
    int stack[DEADLOCK_MAX_PROCESSES];
    int u, depth;
    for (u = 0; u < n; u++) {
        if (color[u] != 0) continue;
        depth = 0;
        if (dfs(u, n, adj, color, stack, &depth, cycle, cycle_len)) return 1;
    }
    *cycle_len = 0;
    return 0;
}

int deadlock_choose_victim(int cycle_len, const int *cycle, const int *held) {
    int best = -1, i;
    for (i = 0; i < cycle_len; i++) {
        int p = cycle[i];
        if (best < 0 || held[p] < held[best] || (held[p] == held[best] && p < best)) best = p;
    }
    return best;
}

static int index_of(const char *name, char names[][33], int n) {
    int i;
    for (i = 0; i < n; i++)
        if (strcmp(names[i], name) == 0) return i;
    return -1;
}

int deadlock_run(const JsonValue *input, Buf *out, char *err, size_t errsz) {
    const JsonValue *plist = json_get(input, "processes");
    const JsonValue *elist = json_get(input, "edges");
    const JsonValue *hlist = json_get(input, "held");
    const char *model = NULL;
    char names[DEADLOCK_MAX_PROCESSES][33];
    int held[DEADLOCK_MAX_PROCESSES] = {0};
    unsigned char adj[DEADLOCK_MAX_PROCESSES * DEADLOCK_MAX_PROCESSES];
    int cycle[DEADLOCK_MAX_PROCESSES], cycle_len = 0, found, victim = -1, n, i;
    size_t k;
    char msg[EBMS_ERR_LEN];
    int multi;

    if (!plist || plist->type != JV_ARRAY) {
        set_error(err, errsz, "'processes' must be a list of process names");
        return RC_INVALID;
    }
    n = (int)plist->count;
    if (n < 1 || n > DEADLOCK_MAX_PROCESSES) {
        set_error(err, errsz, "process count must be between 1 and 100");
        return RC_INVALID;
    }
    for (i = 0; i < n; i++) {
        const JsonValue *e = plist->items[i];
        if (e->type != JV_STRING || !valid_name(e->string)) {
            set_error(err, errsz, "process names must be 1-32 letters, digits, '_' or '-'");
            return RC_INVALID;
        }
        if (index_of(e->string, names, i) >= 0) {
            snprintf(msg, sizeof msg, "duplicate process '%s'", e->string);
            set_error(err, errsz, msg);
            return RC_INVALID;
        }
        snprintf(names[i], sizeof names[i], "%s", e->string);
    }
    if (json_string_field(input, "resource_model", &model, 0, err, errsz) != RC_OK) return RC_INVALID;
    if (model && strcmp(model, "single_instance") != 0 && strcmp(model, "multi_instance") != 0) {
        set_error(err, errsz, "resource_model must be single_instance or multi_instance");
        return RC_INVALID;
    }
    multi = model && strcmp(model, "multi_instance") == 0;

    memset(adj, 0, sizeof adj);
    if (elist) {
        if (elist->type != JV_ARRAY) {
            set_error(err, errsz, "'edges' must be a list");
            return RC_INVALID;
        }
        for (k = 0; k < elist->count; k++) {
            const JsonValue *e = elist->items[k];
            const JsonValue *from, *to;
            int u, v;
            if (e->type != JV_OBJECT) {
                set_error(err, errsz, "each edge must be an object with 'from' and 'to'");
                return RC_INVALID;
            }
            from = json_get(e, "from");
            to = json_get(e, "to");
            if (!from || from->type != JV_STRING || !to || to->type != JV_STRING) {
                set_error(err, errsz, "each edge needs string 'from' and 'to' fields");
                return RC_INVALID;
            }
            u = index_of(from->string, names, n);
            v = index_of(to->string, names, n);
            if (u < 0 || v < 0) {
                set_error(err, errsz, "edge refers to a process that is not in 'processes'");
                return RC_INVALID;
            }
            if (adj[u * n + v]) {
                snprintf(msg, sizeof msg, "duplicate edge %s -> %s", from->string, to->string);
                set_error(err, errsz, msg);
                return RC_INVALID;
            }
            adj[u * n + v] = 1;
        }
    }
    if (hlist) {
        size_t h;
        if (hlist->type != JV_OBJECT) {
            set_error(err, errsz, "'held' must be an object mapping process to units held");
            return RC_INVALID;
        }
        for (h = 0; h < hlist->count; h++) {
            int idx = index_of(hlist->keys[h], names, n);
            const JsonValue *val = hlist->items[h];
            if (idx < 0) {
                set_error(err, errsz, "'held' refers to a process that is not in 'processes'");
                return RC_INVALID;
            }
            if (val->type != JV_INT || val->integer < 0 || val->integer > 10000) {
                set_error(err, errsz, "'held' values must be integers from 0 to 10000");
                return RC_INVALID;
            }
            held[idx] = (int)val->integer;
        }
    }

    found = deadlock_find_cycle(n, adj, cycle, &cycle_len);
    if (found) victim = deadlock_choose_victim(cycle_len, cycle, held);

    buf_printf(out, "{\"ok\":true,\"resource_model\":\"%s\",\"cycle_found\":%s,\"deadlock\":%s,\"cycle\":[",
               multi ? "multi_instance" : "single_instance", found ? "true" : "false",
               (found && !multi) ? "true" : "false");
    for (i = 0; found && i < cycle_len; i++) buf_printf(out, "%s", i ? "," : "") , buf_json_string(out, names[cycle[i]]);
    buf_puts(out, "],\"victim\":");
    if (found) buf_json_string(out, names[victim]);
    else buf_puts(out, "null");
    buf_puts(out, ",\"victim_cost\":");
    if (found) buf_printf(out, "%d", held[victim]);
    else buf_puts(out, "null");
    buf_puts(out, ",\"assumptions\":[");
    buf_json_string(out, "each resource type has one instance unless resource_model is multi_instance");
    buf_puts(out, ",");
    buf_json_string(out, "edge P -> Q means P waits for a resource held by Q");
    buf_puts(out, ",");
    buf_json_string(out, multi
        ? "multi_instance: a cycle is necessary but not sufficient for deadlock, so deadlock is not confirmed"
        : "single_instance: a directed cycle in the wait-for graph is a deadlock");
    buf_puts(out, ",");
    buf_json_string(out, "victim = cycle member holding the fewest units, ties by input order");
    buf_puts(out, "]}");
    return out->oom ? RC_INTERNAL : RC_OK;
}

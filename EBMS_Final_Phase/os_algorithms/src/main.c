/*
 * ebms_os: one command-line engine for all five OS modules.
 *   usage: ebms_os <scheduler|banker|deadlock|producer_consumer|readers_writers>  < input.json
 * stdout: exactly one JSON object.  exit 0 = ok, 2 = invalid input, 3 = internal failure.
 * stderr is reserved for diagnostics and is never part of the JSON contract.
 */
#include "common.h"
#include "json.h"
#include "scheduler.h"
#include "banker.h"
#include "deadlock.h"
#include "producer_consumer.h"
#include "readers_writers.h"

static int write_error(const char *message) {
    Buf b;
    buf_init(&b);
    buf_puts(&b, "{\"ok\":false,\"error\":");
    buf_json_string(&b, message);
    buf_puts(&b, "}\n");
    fwrite(b.data, 1, b.len, stdout);
    buf_free(&b);
    return 0;
}

static int read_input(char **data, size_t *len) {
    size_t cap = 4096, n = 0;
    char *buf = malloc(cap);
    size_t got;
    if (!buf) return RC_INTERNAL;
    for (;;) {
        if (n == cap) {
            char *nb;
            if (cap > EBMS_MAX_INPUT_BYTES) break;
            cap *= 2;
            nb = realloc(buf, cap);
            if (!nb) {
                free(buf);
                return RC_INTERNAL;
            }
            buf = nb;
        }
        got = fread(buf + n, 1, cap - n, stdin);
        n += got;
        if (got == 0) break;
        if (n > EBMS_MAX_INPUT_BYTES) break;
    }
    if (n > EBMS_MAX_INPUT_BYTES) {
        free(buf);
        return RC_INVALID;
    }
    *data = buf;
    *len = n;
    return RC_OK;
}

int main(int argc, char **argv) {
    char err[EBMS_ERR_LEN] = {0};
    char *input = NULL;
    size_t input_len = 0;
    JsonValue *root = NULL;
    Buf out;
    int rc;

    if (argc != 2) {
        write_error("usage: ebms_os <scheduler|banker|deadlock|producer_consumer|readers_writers>");
        return 2;
    }
    rc = read_input(&input, &input_len);
    if (rc == RC_INVALID) {
        write_error("input is larger than 64 KB");
        return 2;
    }
    if (rc != RC_OK) {
        write_error("internal error");
        return 3;
    }
    rc = json_parse(input, input_len, &root, err, sizeof err);
    free(input);
    if (rc != RC_OK) {
        write_error(err);
        return 2;
    }
    if (root->type != JV_OBJECT) {
        json_free(root);
        write_error("input must be a JSON object");
        return 2;
    }

    buf_init(&out);
    if (strcmp(argv[1], "scheduler") == 0) rc = scheduler_run(root, &out, err, sizeof err);
    else if (strcmp(argv[1], "banker") == 0) rc = banker_run(root, &out, err, sizeof err);
    else if (strcmp(argv[1], "deadlock") == 0) rc = deadlock_run(root, &out, err, sizeof err);
    else if (strcmp(argv[1], "producer_consumer") == 0) rc = producer_consumer_run(root, &out, err, sizeof err);
    else if (strcmp(argv[1], "readers_writers") == 0) rc = readers_writers_run(root, &out, err, sizeof err);
    else {
        set_error(err, sizeof err, "unknown module");
        rc = RC_INVALID;
    }
    json_free(root);

    if (rc != RC_OK || out.oom || !out.data) {
        buf_free(&out);
        if (rc == RC_INVALID) {
            write_error(err);
            return 2;
        }
        write_error(err[0] ? err : "internal error");
        return 3;
    }
    fwrite(out.data, 1, out.len, stdout);
    fputc('\n', stdout);
    buf_free(&out);
    return 0;
}

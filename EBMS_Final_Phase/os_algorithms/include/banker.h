/* Banker's algorithm: safety check, safe sequence, and resource-request decision. */
#ifndef EBMS_BANKER_H
#define EBMS_BANKER_H

#include "common.h"
#include "json.h"

#define BANKER_MAX_PROCESSES 50
#define BANKER_MAX_RESOURCES 20
#define BANKER_MAX_VALUE 10000

typedef enum {
    BDEC_GRANTED,
    BDEC_REJECT_EXCEEDS_NEED,
    BDEC_REJECT_INSUFFICIENT,
    BDEC_REJECT_UNSAFE
} BankerCode;

typedef struct {
    BankerCode code;
    int safe_after;
    int seq_after[BANKER_MAX_PROCESSES];
    int seq_after_len;
} BankerDecision;

/* Matrices are flat, row-major: element (i, j) lives at [i * m + j]. */
int banker_validate_state(int n, int m, const int *available, const int *max, const int *allocation,
                          char *err, size_t errsz);
void banker_compute_need(int n, int m, const int *max, const int *allocation, int *need);
int banker_is_safe(int n, int m, const int *available, const int *allocation, const int *need, int *seq,
                   int *seq_len);
int banker_check_request(int n, int m, const int *available, const int *allocation, const int *need, int pid,
                         const int *request, BankerDecision *d);
const char *banker_code_name(BankerCode c);
int banker_run(const JsonValue *input, Buf *out, char *err, size_t errsz);

#endif

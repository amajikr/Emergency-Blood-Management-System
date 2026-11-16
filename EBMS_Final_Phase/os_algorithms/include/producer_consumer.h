/* Bounded-buffer producer-consumer with real threads: mutex + not_full / not_empty condition variables. */
#ifndef EBMS_PRODUCER_CONSUMER_H
#define EBMS_PRODUCER_CONSUMER_H

#include "common.h"
#include "json.h"

#define PC_MAX_CAPACITY 1000
#define PC_MAX_THREADS 16
#define PC_MAX_ITEMS_PER_PRODUCER 100000
#define PC_MAX_TOTAL_ITEMS 200000

typedef struct {
    int capacity;
    int producers;
    int consumers;
    int items_per_producer;
} PCConfig;

typedef struct {
    long long produced;
    long long consumed;
    long long checksum_produced;
    long long checksum_consumed;
    long long max_occupancy;
    long long producer_waits_full;
    long long consumer_waits_empty;
    long long duplicates;
    long long unknown_items;
    long long order_violations;
    int exactly_once;
    int threads_joined;
    int producers;
    int consumers;
    long long *per_producer;
    long long *per_consumer;
} PCResult;

int pc_simulate(const PCConfig *cfg, PCResult *res, char *err, size_t errsz);
void pc_result_free(PCResult *res);
int producer_consumer_run(const JsonValue *input, Buf *out, char *err, size_t errsz);

#endif

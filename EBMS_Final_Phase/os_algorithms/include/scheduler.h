/* CPU scheduling: FCFS, SJF (non-preemptive), SRTF, Priority (NP and P), Round Robin. */
#ifndef EBMS_SCHEDULER_H
#define EBMS_SCHEDULER_H

#include "common.h"
#include "json.h"

typedef enum { ALG_FCFS, ALG_SJF, ALG_SRTF, ALG_PRIORITY_NP, ALG_PRIORITY_P, ALG_RR, ALG_COUNT } SchedAlgo;

#define SCHED_MAX_PROCESSES 100
#define SCHED_MAX_PID 32
#define SCHED_MAX_ARRIVAL 100000
#define SCHED_MAX_BURST 10000
#define SCHED_MAX_TOTAL_BURST 5000 /* keeps the Gantt output small */
#define SCHED_MAX_PRIORITY 1000000
#define SCHED_MAX_QUANTUM 10000
#define SCHED_IDLE (-1)

typedef struct {
    char pid[SCHED_MAX_PID + 1];
    int arrival;
    int burst;
    int priority;
    int first_start;
    int completion;
    int turnaround;
    int waiting;
    int response;
} SchedProcess;

typedef struct {
    int pid; /* index into procs, or SCHED_IDLE */
    int start;
    int end;
} GanttSegment;

typedef struct {
    SchedProcess *procs;
    size_t count;
    GanttSegment *gantt;
    size_t gantt_len;
    size_t gantt_cap;
    int total_time;
} SchedResult;

const char *sched_algo_name(SchedAlgo a);
int sched_parse_algo(const char *name, SchedAlgo *out);
int scheduler_simulate(SchedAlgo algo, int quantum, const SchedProcess *in, size_t n, SchedResult *res,
                       char *err, size_t errsz);
void scheduler_result_free(SchedResult *res);
int scheduler_run(const JsonValue *input, Buf *out, char *err, size_t errsz);

#endif

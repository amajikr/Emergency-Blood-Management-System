/* Deadlock detection on a wait-for graph (DFS cycle search) with victim selection. */
#ifndef EBMS_DEADLOCK_H
#define EBMS_DEADLOCK_H

#include "common.h"
#include "json.h"

#define DEADLOCK_MAX_PROCESSES 100

/* adj is an n*n row-major matrix: adj[u*n+v] = 1 means process u waits for process v.
 * On success returns 1 and fills cycle (node indices, path order) and cycle_len.
 * Returns 0 if the graph is acyclic. Self-loops are cycles of length one. */
int deadlock_find_cycle(int n, const unsigned char *adj, int *cycle, int *cycle_len);

/* Victim = cycle member holding the fewest units; ties go to the lower index (input order). */
int deadlock_choose_victim(int cycle_len, const int *cycle, const int *held);

int deadlock_run(const JsonValue *input, Buf *out, char *err, size_t errsz);

#endif

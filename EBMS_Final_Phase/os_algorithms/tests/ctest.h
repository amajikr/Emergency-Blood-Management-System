/* Tiny assertion harness shared by the C test programs. */
#ifndef EBMS_CTEST_H
#define EBMS_CTEST_H
#include <stdio.h>

static int ct_run = 0, ct_failed = 0;

#define CHECK(cond) do { ct_run++; if (!(cond)) { ct_failed++; \
    fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)
#define CHECK_EQ(a, b) do { ct_run++; if ((long long)(a) != (long long)(b)) { ct_failed++; \
    fprintf(stderr, "FAIL %s:%d: %s == %lld, expected %lld\n", __FILE__, __LINE__, #a, (long long)(a), (long long)(b)); } } while (0)

#define CT_SUMMARY(name) do { printf("%s: %d checks, %d failed\n", (name), ct_run, ct_failed); \
    return ct_failed ? 1 : 0; } while (0)

#endif

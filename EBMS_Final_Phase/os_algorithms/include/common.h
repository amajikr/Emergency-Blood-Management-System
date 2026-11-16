/* Shared constants, growable output buffer, and error helpers for the EBMS OS engine. */
#ifndef EBMS_COMMON_H
#define EBMS_COMMON_H

#if !defined(_WIN32) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Return codes shared by every module. main.c maps them to process exit codes. */
#define RC_OK 0
#define RC_INVALID 1   /* bad user input: exit code 2 */
#define RC_INTERNAL 2  /* allocation or thread failure: exit code 3 */

#define EBMS_MAX_INPUT_BYTES (64 * 1024)
#define EBMS_ERR_LEN 256

/* Growable output buffer. If an allocation fails, oom is set and later appends fail. */
typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int oom;
} Buf;

void buf_init(Buf *b);
void buf_free(Buf *b);
int buf_append(Buf *b, const char *s, size_t n);
int buf_puts(Buf *b, const char *s);
int buf_printf(Buf *b, const char *fmt, ...);
int buf_json_string(Buf *b, const char *s); /* writes a quoted, escaped JSON string */

void set_error(char *err, size_t errsz, const char *msg);
int valid_name(const char *s); /* 1-32 chars: letters, digits, '_' or '-' */

#endif

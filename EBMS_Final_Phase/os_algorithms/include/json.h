/* Minimal bounded JSON parser (integers only, no floats) plus typed field helpers. */
#ifndef EBMS_JSON_H
#define EBMS_JSON_H

#include <stddef.h>
#include "common.h"

#define JSON_MAX_DEPTH 16
#define JSON_MAX_NODES 20000
#define JSON_MAX_STRING 1024

typedef enum { JV_NULL, JV_BOOL, JV_INT, JV_STRING, JV_ARRAY, JV_OBJECT } JsonType;

typedef struct JsonValue {
    JsonType type;
    int boolean;
    long long integer;
    char *string;
    size_t count;
    struct JsonValue **items; /* array elements, or object values */
    char **keys;              /* object keys, NULL for arrays */
} JsonValue;

int json_parse(const char *text, size_t len, JsonValue **out, char *err, size_t errsz);
void json_free(JsonValue *v);
const JsonValue *json_get(const JsonValue *obj, const char *key);

/* Typed readers. Return RC_OK or RC_INVALID with a user-safe message in err. */
int json_int_field(const JsonValue *obj, const char *key, long long lo, long long hi, long long def,
                   int required, long long *out, char *err, size_t errsz);
int json_string_field(const JsonValue *obj, const char *key, const char **out, int required, char *err,
                      size_t errsz);

#endif

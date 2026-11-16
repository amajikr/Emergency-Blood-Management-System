#include <ctype.h>
#include "json.h"

typedef struct {
    const char *s;
    size_t n;
    size_t pos;
    int depth;
    size_t nodes;
    char *err;
    size_t errsz;
} Parser;

static int fail(Parser *p, const char *msg) {
    snprintf(p->err, p->errsz, "%s at position %zu", msg, p->pos);
    return -1;
}

static void skip_ws(Parser *p) {
    while (p->pos < p->n && (p->s[p->pos] == ' ' || p->s[p->pos] == '\t' || p->s[p->pos] == '\r' || p->s[p->pos] == '\n'))
        p->pos++;
}

static JsonValue *new_node(Parser *p, JsonType t) {
    JsonValue *v;
    if (p->nodes >= JSON_MAX_NODES) return NULL;
    v = calloc(1, sizeof *v);
    if (v) {
        v->type = t;
        p->nodes++;
    }
    return v;
}

void json_free(JsonValue *v) {
    size_t i;
    if (!v) return;
    free(v->string);
    for (i = 0; i < v->count; i++) {
        json_free(v->items[i]);
        if (v->keys) free(v->keys[i]);
    }
    free(v->items);
    free(v->keys);
    free(v);
}

static int push_child(JsonValue *v, JsonValue *child, char *key) {
    JsonValue **items;
    char **keys = NULL;
    size_t i;
    if (v->type == JV_OBJECT) {
        for (i = 0; i < v->count; i++) {
            if (strcmp(v->keys[i], key) == 0) return -2; /* duplicate key */
        }
    }
    items = realloc(v->items, (v->count + 1) * sizeof *items);
    if (!items) return -1;
    v->items = items;
    if (v->type == JV_OBJECT) {
        keys = realloc(v->keys, (v->count + 1) * sizeof *keys);
        if (!keys) return -1;
        v->keys = keys;
        v->keys[v->count] = key;
    }
    v->items[v->count] = child;
    v->count++;
    return 0;
}

/* Parses a JSON string token into a freshly allocated C string. */
static int parse_raw_string(Parser *p, char **out) {
    Buf b;
    unsigned cp;
    int i;
    buf_init(&b);
    if (p->s[p->pos] != '"') return fail(p, "expected string");
    p->pos++;
    while (1) {
        unsigned char c;
        if (p->pos >= p->n) {
            buf_free(&b);
            return fail(p, "unterminated string");
        }
        c = (unsigned char)p->s[p->pos];
        if (b.len > JSON_MAX_STRING) {
            buf_free(&b);
            return fail(p, "string too long");
        }
        if (c == '"') {
            p->pos++;
            break;
        }
        if (c < 0x20) {
            buf_free(&b);
            return fail(p, "control character in string");
        }
        if (c != '\\') {
            buf_append(&b, (const char *)&c, 1);
            p->pos++;
            continue;
        }
        p->pos++;
        if (p->pos >= p->n) {
            buf_free(&b);
            return fail(p, "bad escape");
        }
        c = (unsigned char)p->s[p->pos++];
        switch (c) {
        case '"': buf_append(&b, "\"", 1); break;
        case '\\': buf_append(&b, "\\", 1); break;
        case '/': buf_append(&b, "/", 1); break;
        case 'b': buf_append(&b, "\b", 1); break;
        case 'f': buf_append(&b, "\f", 1); break;
        case 'n': buf_append(&b, "\n", 1); break;
        case 'r': buf_append(&b, "\r", 1); break;
        case 't': buf_append(&b, "\t", 1); break;
        case 'u':
            cp = 0;
            for (i = 0; i < 4; i++) {
                char h;
                if (p->pos >= p->n) {
                    buf_free(&b);
                    return fail(p, "bad unicode escape");
                }
                h = p->s[p->pos++];
                if (!isxdigit((unsigned char)h)) {
                    buf_free(&b);
                    return fail(p, "bad unicode escape");
                }
                cp = cp * 16 + (unsigned)(isdigit((unsigned char)h) ? h - '0' : (tolower((unsigned char)h) - 'a' + 10));
            }
            if (cp >= 0xD800 && cp <= 0xDFFF) {
                buf_free(&b);
                return fail(p, "surrogate escapes are not supported");
            }
            if (cp < 0x80) {
                char ch = (char)cp;
                buf_append(&b, &ch, 1);
            } else if (cp < 0x800) {
                char ch[2];
                ch[0] = (char)(0xC0 | (cp >> 6));
                ch[1] = (char)(0x80 | (cp & 0x3F));
                buf_append(&b, ch, 2);
            } else {
                char ch[3];
                ch[0] = (char)(0xE0 | (cp >> 12));
                ch[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
                ch[2] = (char)(0x80 | (cp & 0x3F));
                buf_append(&b, ch, 3);
            }
            break;
        default:
            buf_free(&b);
            return fail(p, "bad escape");
        }
    }
    if (b.oom) {
        buf_free(&b);
        return -1;
    }
    if (!b.data) buf_append(&b, "", 0);
    *out = b.data;
    return 0;
}

static int parse_value(Parser *p, JsonValue **out);

static int parse_number(Parser *p, JsonValue **out) {
    size_t start = p->pos;
    int neg = 0, digits = 0;
    long long acc = 0;
    JsonValue *v;
    if (p->s[p->pos] == '-') {
        neg = 1;
        p->pos++;
    }
    if (p->pos >= p->n || !isdigit((unsigned char)p->s[p->pos])) return fail(p, "bad number");
    if (p->s[p->pos] == '0' && p->pos + 1 < p->n && isdigit((unsigned char)p->s[p->pos + 1]))
        return fail(p, "leading zeros are not allowed");
    while (p->pos < p->n && isdigit((unsigned char)p->s[p->pos])) {
        acc = acc * 10 + (p->s[p->pos] - '0');
        digits++;
        p->pos++;
        if (digits > 15) return fail(p, "number too large");
    }
    if (p->pos < p->n && (p->s[p->pos] == '.' || p->s[p->pos] == 'e' || p->s[p->pos] == 'E')) {
        return fail(p, "only integer numbers are supported");
    }
    (void)start;
    v = new_node(p, JV_INT);
    if (!v) return fail(p, "too many values");
    v->integer = neg ? -acc : acc;
    *out = v;
    return 0;
}

static int match_literal(Parser *p, const char *lit) {
    size_t L = strlen(lit);
    if (p->pos + L > p->n || strncmp(p->s + p->pos, lit, L) != 0) return 0;
    p->pos += L;
    return 1;
}

static int parse_object(Parser *p, JsonValue **out) {
    JsonValue *v = new_node(p, JV_OBJECT);
    if (!v) return fail(p, "too many values");
    p->pos++; /* '{' */
    if (++p->depth > JSON_MAX_DEPTH) {
        json_free(v);
        return fail(p, "nesting too deep");
    }
    skip_ws(p);
    if (p->pos < p->n && p->s[p->pos] == '}') {
        p->pos++;
        p->depth--;
        *out = v;
        return 0;
    }
    for (;;) {
        char *key = NULL;
        JsonValue *child = NULL;
        int rc;
        skip_ws(p);
        if (p->pos >= p->n || p->s[p->pos] != '"') {
            json_free(v);
            return fail(p, "expected object key");
        }
        if (parse_raw_string(p, &key) != 0) {
            json_free(v);
            return -1;
        }
        skip_ws(p);
        if (p->pos >= p->n || p->s[p->pos] != ':') {
            free(key);
            json_free(v);
            return fail(p, "expected ':'");
        }
        p->pos++;
        if (parse_value(p, &child) != 0) {
            free(key);
            json_free(v);
            return -1;
        }
        rc = push_child(v, child, key);
        if (rc != 0) {
            json_free(child);
            free(key);
            json_free(v);
            return rc == -2 ? fail(p, "duplicate key") : fail(p, "out of memory");
        }
        skip_ws(p);
        if (p->pos < p->n && p->s[p->pos] == ',') {
            p->pos++;
            continue;
        }
        if (p->pos < p->n && p->s[p->pos] == '}') {
            p->pos++;
            break;
        }
        json_free(v);
        return fail(p, "expected ',' or '}'");
    }
    p->depth--;
    *out = v;
    return 0;
}

static int parse_array(Parser *p, JsonValue **out) {
    JsonValue *v = new_node(p, JV_ARRAY);
    if (!v) return fail(p, "too many values");
    p->pos++; /* '[' */
    if (++p->depth > JSON_MAX_DEPTH) {
        json_free(v);
        return fail(p, "nesting too deep");
    }
    skip_ws(p);
    if (p->pos < p->n && p->s[p->pos] == ']') {
        p->pos++;
        p->depth--;
        *out = v;
        return 0;
    }
    for (;;) {
        JsonValue *child = NULL;
        skip_ws(p);
        if (parse_value(p, &child) != 0) {
            json_free(v);
            return -1;
        }
        if (push_child(v, child, NULL) != 0) {
            json_free(child);
            json_free(v);
            return fail(p, "out of memory");
        }
        skip_ws(p);
        if (p->pos < p->n && p->s[p->pos] == ',') {
            p->pos++;
            continue;
        }
        if (p->pos < p->n && p->s[p->pos] == ']') {
            p->pos++;
            break;
        }
        json_free(v);
        return fail(p, "expected ',' or ']'");
    }
    p->depth--;
    *out = v;
    return 0;
}

static int parse_value(Parser *p, JsonValue **out) {
    JsonValue *v;
    char *str;
    skip_ws(p);
    if (p->pos >= p->n) return fail(p, "unexpected end of input");
    switch (p->s[p->pos]) {
    case '{': return parse_object(p, out);
    case '[': return parse_array(p, out);
    case '"':
        if (parse_raw_string(p, &str) != 0) return -1;
        v = new_node(p, JV_STRING);
        if (!v) {
            free(str);
            return fail(p, "too many values");
        }
        v->string = str;
        *out = v;
        return 0;
    case 't':
        if (!match_literal(p, "true")) return fail(p, "bad literal");
        v = new_node(p, JV_BOOL);
        if (!v) return fail(p, "too many values");
        v->boolean = 1;
        *out = v;
        return 0;
    case 'f':
        if (!match_literal(p, "false")) return fail(p, "bad literal");
        v = new_node(p, JV_BOOL);
        if (!v) return fail(p, "too many values");
        v->boolean = 0;
        *out = v;
        return 0;
    case 'n':
        if (!match_literal(p, "null")) return fail(p, "bad literal");
        v = new_node(p, JV_NULL);
        if (!v) return fail(p, "too many values");
        *out = v;
        return 0;
    default:
        if (p->s[p->pos] == '-' || isdigit((unsigned char)p->s[p->pos])) return parse_number(p, out);
        return fail(p, "unexpected character");
    }
}

int json_parse(const char *text, size_t len, JsonValue **out, char *err, size_t errsz) {
    Parser p;
    JsonValue *root = NULL;
    p.s = text;
    p.n = len;
    p.pos = 0;
    p.depth = 0;
    p.nodes = 0;
    p.err = err;
    p.errsz = errsz;
    *out = NULL;
    if (parse_value(&p, &root) != 0) return RC_INVALID;
    skip_ws(&p);
    if (p.pos != p.n) {
        json_free(root);
        fail(&p, "unexpected data after JSON value");
        return RC_INVALID;
    }
    *out = root;
    return RC_OK;
}

const JsonValue *json_get(const JsonValue *obj, const char *key) {
    size_t i;
    if (!obj || obj->type != JV_OBJECT) return NULL;
    for (i = 0; i < obj->count; i++) {
        if (strcmp(obj->keys[i], key) == 0) return obj->items[i];
    }
    return NULL;
}

int json_int_field(const JsonValue *obj, const char *key, long long lo, long long hi, long long def,
                   int required, long long *out, char *err, size_t errsz) {
    const JsonValue *v = json_get(obj, key);
    char msg[EBMS_ERR_LEN];
    if (!v) {
        if (required) {
            snprintf(msg, sizeof msg, "'%s' is required", key);
            set_error(err, errsz, msg);
            return RC_INVALID;
        }
        *out = def;
        return RC_OK;
    }
    if (v->type != JV_INT || v->integer < lo || v->integer > hi) {
        snprintf(msg, sizeof msg, "'%s' must be an integer from %lld to %lld", key, lo, hi);
        set_error(err, errsz, msg);
        return RC_INVALID;
    }
    *out = v->integer;
    return RC_OK;
}

int json_string_field(const JsonValue *obj, const char *key, const char **out, int required, char *err,
                      size_t errsz) {
    const JsonValue *v = json_get(obj, key);
    char msg[EBMS_ERR_LEN];
    if (!v) {
        if (required) {
            snprintf(msg, sizeof msg, "'%s' is required", key);
            set_error(err, errsz, msg);
            return RC_INVALID;
        }
        *out = NULL;
        return RC_OK;
    }
    if (v->type != JV_STRING) {
        snprintf(msg, sizeof msg, "'%s' must be a string", key);
        set_error(err, errsz, msg);
        return RC_INVALID;
    }
    *out = v->string;
    return RC_OK;
}

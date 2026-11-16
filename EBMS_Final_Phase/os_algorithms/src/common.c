#include <stdarg.h>
#include "common.h"

void buf_init(Buf *b) {
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
    b->oom = 0;
}

void buf_free(Buf *b) {
    free(b->data);
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
}

static int buf_reserve(Buf *b, size_t extra) {
    size_t want;
    char *p;
    if (b->oom) return -1;
    if (b->len + extra + 1 <= b->cap) return 0;
    want = b->cap ? b->cap : 256;
    while (want < b->len + extra + 1) {
        if (want > ((size_t)-1) / 2) {
            b->oom = 1;
            return -1;
        }
        want *= 2;
    }
    p = realloc(b->data, want);
    if (!p) {
        b->oom = 1;
        return -1;
    }
    b->data = p;
    b->cap = want;
    return 0;
}

int buf_append(Buf *b, const char *s, size_t n) {
    if (buf_reserve(b, n) != 0) return -1;
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = '\0';
    return 0;
}

int buf_puts(Buf *b, const char *s) {
    return buf_append(b, s, strlen(s));
}

int buf_printf(Buf *b, const char *fmt, ...) {
    va_list ap, ap2;
    int n;
    va_start(ap, fmt);
    va_copy(ap2, ap);
    n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) {
        va_end(ap2);
        return -1;
    }
    if (buf_reserve(b, (size_t)n) != 0) {
        va_end(ap2);
        return -1;
    }
    vsnprintf(b->data + b->len, (size_t)n + 1, fmt, ap2);
    va_end(ap2);
    b->len += (size_t)n;
    return 0;
}

int buf_json_string(Buf *b, const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    if (buf_puts(b, "\"") != 0) return -1;
    for (; *p; p++) {
        switch (*p) {
        case '"': buf_puts(b, "\\\""); break;
        case '\\': buf_puts(b, "\\\\"); break;
        case '\n': buf_puts(b, "\\n"); break;
        case '\r': buf_puts(b, "\\r"); break;
        case '\t': buf_puts(b, "\\t"); break;
        default:
            if (*p < 0x20) {
                buf_printf(b, "\\u%04x", (unsigned)*p);
            } else {
                buf_append(b, (const char *)p, 1);
            }
        }
    }
    return buf_puts(b, "\"");
}

void set_error(char *err, size_t errsz, const char *msg) {
    if (err && errsz > 0) {
        snprintf(err, errsz, "%s", msg);
    }
}

int valid_name(const char *s) {
    size_t i, n;
    if (!s) return 0;
    n = strlen(s);
    if (n < 1 || n > 32) return 0;
    for (i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return 0;
    }
    return 1;
}

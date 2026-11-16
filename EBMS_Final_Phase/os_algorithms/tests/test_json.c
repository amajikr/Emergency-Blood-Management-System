#include "ctest.h"
#include "json.h"

static int parse_ok(const char *s) {
    JsonValue *v = NULL;
    char err[EBMS_ERR_LEN];
    int rc = json_parse(s, strlen(s), &v, err, sizeof err);
    json_free(v);
    return rc == RC_OK;
}

int main(void) {
    JsonValue *v = NULL;
    char err[EBMS_ERR_LEN];
    const char *doc = "{\"a\":1,\"b\":[true,false,null],\"s\":\"x\\n\\u00e9\"}";

    CHECK(parse_ok(doc));
    CHECK(json_parse(doc, strlen(doc), &v, err, sizeof err) == RC_OK);
    CHECK(json_get(v, "a") && json_get(v, "a")->integer == 1);
    CHECK(json_get(v, "b") && json_get(v, "b")->count == 3);
    CHECK(json_get(v, "s") && strcmp(json_get(v, "s")->string, "x\n\xc3\xa9") == 0);
    CHECK(json_get(v, "missing") == NULL);
    json_free(v);

    CHECK(parse_ok("-42"));
    CHECK(parse_ok("[]"));
    CHECK(parse_ok("{}"));
    CHECK(!parse_ok(""));              /* empty */
    CHECK(!parse_ok("{"));             /* truncated */
    CHECK(!parse_ok("{\"a\":1,}"));    /* trailing comma */
    CHECK(!parse_ok("{\"a\":1} x"));   /* trailing data */
    CHECK(!parse_ok("{\"a\":1.5}"));   /* floats not supported */
    CHECK(!parse_ok("{\"a\":1e3}"));   /* exponent not supported */
    CHECK(!parse_ok("{\"a\":01}"));    /* leading zero */
    CHECK(!parse_ok("{\"a\":1,\"a\":2}")); /* duplicate key */
    CHECK(!parse_ok("\"bad \\q escape\""));
    CHECK(!parse_ok("\"\\ud800\""));   /* surrogate */
    CHECK(!parse_ok("\"raw\ncontrol\""));
    CHECK(!parse_ok("tru"));
    CHECK(!parse_ok("99999999999999999999")); /* too large */

    {   /* nesting depth limit */
        char deep[256];
        size_t i;
        for (i = 0; i < 40; i++) deep[i] = '[';
        deep[40] = '\0';
        CHECK(!parse_ok(deep));
    }
    {   /* integer fields and their ranges */
        long long out = 0;
        JsonValue *o = NULL;
        CHECK(json_parse("{\"n\":5}", 7, &o, err, sizeof err) == RC_OK);
        CHECK(json_int_field(o, "n", 1, 10, 0, 1, &out, err, sizeof err) == RC_OK && out == 5);
        CHECK(json_int_field(o, "n", 6, 10, 0, 1, &out, err, sizeof err) == RC_INVALID);
        CHECK(json_int_field(o, "absent", 0, 10, 7, 0, &out, err, sizeof err) == RC_OK && out == 7);
        CHECK(json_int_field(o, "absent", 0, 10, 7, 1, &out, err, sizeof err) == RC_INVALID);
        json_free(o);
    }
    CHECK(valid_name("P-1_a") && !valid_name("") && !valid_name("bad name") && !valid_name("this_name_is_way_too_long_for_the_limit"));

    CT_SUMMARY("test_json");
}

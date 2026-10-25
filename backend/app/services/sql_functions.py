"""Function lists for the classifier, kept apart so `classifier.py` stays readable.

sqlglot gives some Postgres functions their own node type and leaves the rest as `Anonymous`.
Anonymous functions must be on `SAFE_ANONYMOUS_FUNCTIONS`; any other one is unknown, so it counts
as volatile and a statement using it is a write (fail closed). All names are lower case.
"""

# Replaced by one literal taken at intercept time (backend plan step 8). The keyword forms
# (`current_timestamp`, `current_date`, ...) are typed nodes in sqlglot; these four are Anonymous.
PINNED_TIME_FUNCTIONS = frozenset(
    {"now", "statement_timestamp", "clock_timestamp", "transaction_timestamp"}
)

# Different on every call, so a preview cannot predict the commit. Allowed in INSERT values.
VOLATILE_FUNCTIONS = frozenset(
    {
        "random",
        "rand",
        "random_normal",
        "setseed",
        "gen_random_uuid",
        "uuid",
        "uuid_generate_v1",
        "uuid_generate_v4",
        "gen_random_bytes",
        "nextval",
        "timeofday",
        "txid_current",
    }
)

# Side effects even inside a SELECT: ending sessions, taking locks, changing settings, moving
# sequences, sending notifications, running SQL given as text, reaching other servers or files.
# Taken from step 6's `check_read` list (D-103, D-121), which stays as it is until step 17.
SIDE_EFFECT_FUNCTIONS = frozenset(
    {
        "pg_terminate_backend",
        "pg_cancel_backend",
        "set_config",
        "nextval",
        "setval",
        "pg_notify",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "pg_stat_file",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_promote",
        "pg_create_restore_point",
        "pg_logical_emit_message",
        "pg_file_write",
        "pg_start_backup",
        "pg_stop_backup",
        "pg_backup_start",
        "pg_backup_stop",
    }
)
SIDE_EFFECT_PREFIXES = (
    "pg_advisory_",
    "pg_try_advisory_",
    "dblink",
    "lo_",
    "pg_read_",
    "pg_ls_",
    "pg_replication_",
    "pg_wal_replay_",
)
# `pg_create_physical_replication_slot`, `pg_drop_replication_slot`, ...
SIDE_EFFECT_SUBSTRINGS = ("_replication_slot",)

# The allowlist: common functions sqlglot leaves untyped (and, behind `pg_catalog.`, typed ones
# too). `pg_sleep` is on it on purpose; the 5 s statement timeout stops it.
SAFE_ANONYMOUS_FUNCTIONS = frozenset(
    {
        # strings
        "upper", "lower", "initcap", "length", "char_length", "character_length", "octet_length",
        "bit_length", "ascii", "chr", "btrim", "ltrim", "rtrim", "trim", "lpad", "rpad", "repeat",
        "reverse", "left", "right", "replace", "translate", "split_part", "strpos", "position",
        "starts_with", "substring", "substr", "overlay", "concat", "concat_ws", "format", "md5",
        "sha224", "sha256", "sha384", "sha512", "encode", "decode", "convert_from", "convert_to",
        "to_hex", "quote_ident", "quote_literal", "quote_nullable", "normalize", "to_ascii",
        "regexp_match", "regexp_matches", "regexp_replace", "regexp_split_to_array",
        "regexp_split_to_table", "regexp_count", "regexp_instr", "regexp_like", "regexp_substr",
        "string_to_array", "string_agg", "similar_to_escape",
        # numbers
        "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "sqrt", "cbrt", "power", "pow",
        "exp", "ln", "log", "log10", "mod", "div", "gcd", "lcm", "greatest", "least",
        "width_bucket", "scale", "degrees", "radians", "pi", "factorial", "bit_count",
        # dates and times (the clock itself is pinned, not listed here)
        "to_char", "to_date", "to_timestamp", "to_number", "date_trunc", "date_part", "extract",
        "age", "make_date", "make_time", "make_timestamp", "make_timestamptz", "make_interval",
        "justify_days", "justify_hours", "justify_interval", "isfinite", "timezone", "date_bin",
        "date_add", "date_subtract",
        # json
        "json_build_object", "jsonb_build_object", "json_build_array", "jsonb_build_array",
        "to_json", "to_jsonb", "row_to_json", "json_agg", "jsonb_agg", "json_object_agg",
        "jsonb_object_agg", "json_extract_path", "jsonb_extract_path", "json_extract_path_text",
        "jsonb_extract_path_text", "json_array_elements", "json_array_elements_text",
        "jsonb_array_elements", "jsonb_array_elements_text", "json_array_length",
        "jsonb_array_length", "json_each", "json_each_text", "jsonb_each", "jsonb_each_text",
        "json_object_keys", "jsonb_object_keys", "json_typeof", "jsonb_typeof", "jsonb_pretty",
        "json_strip_nulls", "jsonb_strip_nulls", "jsonb_set", "jsonb_insert", "jsonb_path_query",
        "jsonb_path_query_array", "jsonb_path_query_first", "jsonb_path_exists",
        "jsonb_path_match", "json_populate_record", "jsonb_populate_record", "json_to_record",
        "jsonb_to_record",
        # arrays and ranges
        "array_agg", "array_append", "array_cat", "array_dims", "array_fill", "array_length",
        "array_lower", "array_upper", "array_ndims", "array_position", "array_positions",
        "array_prepend", "array_remove", "array_replace", "array_to_string", "cardinality",
        "unnest", "generate_series", "generate_subscripts", "trim_array", "int4range",
        "int8range", "numrange", "daterange", "tsrange", "tstzrange",
        # aggregates and window functions
        "count", "sum", "avg", "min", "max", "bool_and", "bool_or", "every", "stddev", "variance",
        "stddev_pop", "stddev_samp", "var_pop", "var_samp", "corr", "covar_pop", "covar_samp",
        "percentile_cont", "percentile_disc", "mode", "rank", "dense_rank", "percent_rank",
        "cume_dist", "ntile", "lag", "lead", "first_value", "last_value", "nth_value", "row_number",
        # full-text search
        "to_tsvector", "to_tsquery", "plainto_tsquery", "websearch_to_tsquery", "ts_rank",
        # other reads
        "coalesce", "nullif", "pg_typeof", "pg_sleep", "pg_backend_pid", "current_setting",
        "to_regclass", "pg_size_pretty", "pg_column_size", "pg_relation_size",
        "pg_total_relation_size", "pg_table_size", "pg_database_size", "obj_description",
        "col_description", "has_table_privilege", "has_schema_privilege", "version",
    }
)  # fmt: skip

# Strings Postgres turns into the current time when cast (`'now'::timestamptz`). They cannot be
# pinned reliably, so they count as volatile.
SPECIAL_DATETIME_STRINGS = frozenset({"now", "today", "tomorrow", "yesterday"})


def is_side_effect_function(name: str) -> bool:
    return (
        name in SIDE_EFFECT_FUNCTIONS
        or name.startswith(SIDE_EFFECT_PREFIXES)
        or any(part in name for part in SIDE_EFFECT_SUBSTRINGS)
    )

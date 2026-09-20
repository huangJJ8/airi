"""Internal SQL allowlist: no public arbitrary query or metadata browsing surface."""

CAPABILITIES = {
    "connectivity": ("SELECT 1 AS value", 1),
    "date_literal": ("SELECT DATE '2026-09-09' AS value", "2026-09-09"),
    "case": ("SELECT CASE WHEN 1 = 1 THEN 7 ELSE 0 END AS value", 7),
    "coalesce": ("SELECT COALESCE(NULL, 7) AS value", 7),
    "null_safe_equals": ("SELECT CASE WHEN NULL <=> NULL THEN 1 ELSE 0 END AS value", 1),
    "cte": ("WITH x AS (SELECT 7 AS n) SELECT n AS value FROM x", 7),
    "full_outer_join": (
        "SELECT COUNT(*) AS value FROM (SELECT 1 AS n) a "
        "FULL OUTER JOIN (SELECT 2 AS n) b ON a.n = b.n",
        2,
    ),
    "count": ("SELECT COUNT(*) AS value FROM VALUES (1), (NULL), (1) AS t(n)", 3),
    "sum": ("SELECT SUM(n) AS value FROM VALUES (1), (2), (NULL) AS t(n)", 3),
}
INTERNAL = {
    **{k: v[0] for k, v in CAPABILITIES.items()},
    "session": "SELECT version() AS engine_version, current_timezone() AS session_timezone",
    "decimal": "SELECT SUM(n) AS value, typeof(SUM(n)) AS result_type "
    "FROM VALUES (CAST('0.10' AS DECIMAL(18,2))), "
    "(CAST('0.20' AS DECIMAL(18,2))) AS t(n)",
    "resource": "SELECT id AS value FROM range(11)",
    "cancel": "SELECT 1 AS value",
    "fixture_rows": "SELECT seller_tax_no, invoice_date, invoice_amt, dt "
    "FROM tmp_db.airi_invoice_fixture LIMIT 1001",
}

# Phase 8: read-only probes used to fingerprint a *production* runtime. Each one
# is best-effort - a capability the cluster does not expose is reported as
# unsupported rather than being replaced by a guess.
PRODUCTION_PROBES: dict[str, str] = {
    "connectivity": "SELECT 1 AS value",
    "session": "SELECT version() AS engine_version, current_timezone() AS session_timezone",
    "current_database": "SELECT current_database() AS current_database",
    "runtime_user": "SELECT current_user() AS runtime_user",
    "managed_runtime_user": "SELECT logged_in_user() AS runtime_user",
    "catalog": "SELECT current_catalog() AS catalog_identity",
    "cancel": "SELECT 1 AS value",
    "read_only_marker": "SELECT 'airi_read_only_probe' AS marker",
}


def probe_sql(name, mapping=False):
    if name == "source_schema":
        return "DESCRIBE c_db.source_fp_jdc_view"
    if name == "fixture_schema" and mapping:
        return "DESCRIBE tmp_db.airi_invoice_fixture"
    if name == "fixture_rows" and not mapping:
        raise ValueError("Fixture mapping is not enabled")
    if name not in INTERNAL:
        raise ValueError("Probe is not allowlisted")
    return INTERNAL[name]


def production_probe_sql(name):
    if name not in PRODUCTION_PROBES:
        raise ValueError("Production probe is not allowlisted")
    return PRODUCTION_PROBES[name]

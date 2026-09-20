import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from airi.core.execution import SHANGHAI
from airi.execution.sandbox import validate_sql
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.testing.failure import classify
from airi.testing.models import MetricTestReport, MetricTestResult
from airi.testing.reconciliation import reconciles, reference_calculate, reference_join_count
from airi.testing.relation import PROBES


def expected_columns(name):
    if name == "invoice_amount_growth_30d":
        return ["entity_id", "current_amount", "previous_amount", name]
    return ["entity_id", name]


def integer_valued(metric) -> bool:
    """Counting metrics report integers; SUM and derived growth report numerics."""
    aggregation = getattr(metric, "aggregation", None)
    return aggregation is not None and aggregation.function in {"count", "count_distinct"}


def schema_matches(execution, metric):
    names = expected_columns(metric.name)
    if [c.name for c in execution.columns] != names:
        return False
    for column in execution.columns:
        kind = column.type.lower()
        if column.name == "entity_id":
            valid = kind in {"string", "varchar", "char"}
        elif column.name == metric.name and integer_valued(metric):
            valid = kind in {"bigint", "int", "integer", "smallint", "tinyint"}
        else:
            valid = bool(
                re.fullmatch(
                    r"decimal(?:\(\d+,\s*\d+\))?|double|float|"
                    r"bigint|int|integer|smallint|tinyint",
                    kind,
                )
            )
        if not valid:
            return False
    return all(list(row) == names for row in execution.rows)


def joined_rows_resolve(execution) -> bool:
    """A joined result must carry the entity key and the aggregate column per row.

    Column names alone do not prove the join produced usable rows; every row has
    to actually resolve to an entity value and an aggregate value.
    """
    names = {column.name for column in execution.columns}
    if not {"entity_id"}.issubset(names):
        return False
    return all(
        row.get("entity_id") is not None and len(row) == len(names) for row in execution.rows
    )


def boundary_matches(code, metric, context):
    """Execute isolated boundary probes, not a predicate substring check."""
    anchor = context.anchor_time.astimezone(SHANGHAI).date()
    rows = [
        {
            "seller_tax_no": f"boundary_{offset}",
            "invoice_date": (anchor - timedelta(days=offset)).isoformat(),
            "invoice_amt": "11",
            "dt": "unused",
        }
        for offset in sorted(
            {
                0,
                1,
                30,
                31,
                59,
                60,
                61,
                *(
                    {metric.window.size - 1, metric.window.size, metric.window.size + 1}
                    if getattr(metric, "window", None)
                    else set()
                ),
            }
        )
    ]
    actual = MockQueryExecutor.evaluate(code, rows).rows
    return reconciles(actual, reference_calculate(metric.name, context, rows))


class MetricTestRunner:
    def run(self, plan, execution, metric, code, rules, fixture_rows=None, relation_tables=None):
        started = datetime.now(UTC)
        results = []
        for case in plan.tests:
            status, category, summary = "passed", None, "Check passed"
            if execution.status != "success":
                status, category, summary = (
                    "skipped",
                    execution.failure_category,
                    "Execution failed",
                )
            elif case.type == "schema":
                if not schema_matches(execution, metric):
                    category, summary = "schema_error", "Column names, order, or types mismatch"
                elif getattr(metric, "joins", None) and not joined_rows_resolve(execution):
                    category, summary = (
                        "schema_error",
                        "Joined rows do not carry the projected entity and metric aliases",
                    )
            elif case.type == "window":
                if not validate_sql(code, metric, execution.execution_context).valid:
                    category, summary = "window_mismatch", "SQL differs from approved windows"
            elif case.type == "boundary":
                try:
                    valid = boundary_matches(code, metric, execution.execution_context)
                except Exception:
                    valid = False
                if not valid:
                    category, summary = "boundary_mismatch", "Boundary fixture differs"
                else:
                    summary = "SQLite boundary probes passed; not live Spark evidence"
            elif case.type in PROBES:
                if not getattr(metric, "joins", None):
                    status, summary = (
                        "skipped",
                        "Relationship probe does not apply to a single-source metric",
                    )
                else:
                    try:
                        category, summary = PROBES[case.type](code)
                    except Exception:
                        category, summary = "execution_error", "Relationship probe failed to run"
            elif execution.row_count == 0:
                status, category, summary = (
                    "warning" if rules.empty_result.severity == "warning" else "failed",
                    "empty_result",
                    "Execution succeeded with zero rows",
                )
            elif case.type == "null":
                count = sum(row.get("entity_id") is None for row in execution.rows)
                if count:
                    status = "warning" if case.severity == "warning" else "failed"
                    summary = f"Observed {count} null entity rows"
                elif execution.truncated:
                    status, summary = (
                        "skipped",
                        "Incomplete result cannot establish absence of nulls",
                    )
            elif case.type == "duplicate":
                duplicates = sum(
                    n - 1 for n in Counter(row.get("entity_id") for row in execution.rows).values()
                )
                if duplicates:
                    category, summary = (
                        "duplicate_entity",
                        f"Observed {duplicates} extra entity rows",
                    )
                elif execution.truncated:
                    status, summary = "skipped", "Incomplete result cannot establish uniqueness"
            elif case.type == "reconciliation":
                category, status, summary = self._reconcile(
                    metric, execution, fixture_rows, relation_tables
                )
            if category and status == "passed":
                status = "failed" if case.severity == "error" else "warning"
            results.append(
                MetricTestResult(
                    **case.model_dump(),
                    status=status,
                    failure_category=category,
                    expected_summary="Conform to approved metric semantics",
                    actual_summary=summary,
                )
            )
        counts = Counter(r.status for r in results)
        status = (
            "failed"
            if counts["failed"] or execution.status != "success"
            else "passed_with_warnings"
            if counts["warning"] or counts["skipped"]
            else "passed"
        )
        return MetricTestReport(
            execution_run_id=execution.execution_run_id,
            artifact_id=execution.artifact_id,
            status=status,
            total=len(results),
            passed=counts["passed"],
            warnings=counts["warning"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            created_at=started,
            finished_at=datetime.now(UTC),
            plan=plan,
            results=results,
            classifications=[
                classify(c)
                for c in sorted({r.failure_category for r in results if r.failure_category})
            ],
        )

    @staticmethod
    def _reconcile(metric, execution, fixture_rows, relation_tables):
        """Compare against the oracle that owns this metric's shape."""
        if execution.truncated:
            return None, "skipped", "Complete matching reference dataset unavailable"
        if getattr(metric, "joins", None):
            if relation_tables is None:
                return None, "skipped", "No independent relationship reference available"
            expected = reference_join_count(metric, relation_tables)
        elif fixture_rows is not None:
            expected = reference_calculate(metric.name, execution.execution_context, fixture_rows)
        else:
            return None, "skipped", "Complete matching reference dataset unavailable"
        if not reconciles(execution.rows, expected):
            return "reconciliation_mismatch", "passed", "Independent reference differs"
        return None, "passed", "Independent reference matches"

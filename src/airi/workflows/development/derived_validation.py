import re

from airi.core.execution import ExecutionContext
from airi.metric_ir.derived import DerivedMetricIR
from airi.workflows.development.derived_planning import DerivedMetricPlanner
from airi.workflows.development.validation import FORBIDDEN, SQLValidationResult, SQLValidator

# Closed composition grammar. Inner CTEs are independently checked by the atomic validator.
CTES = re.compile(
    r"\s*WITH\s+current_period\s+AS\s*\((?P<current>.*?)\)\s*,\s*"
    r"previous_period\s+AS\s*\((?P<previous>.*?)\)\s*(?P<select>SELECT.*)",
    re.I | re.S,
)
TAIL = re.compile(
    r"SELECT COALESCE\(c\.entity_id, p\.entity_id\) AS entity_id, "
    r"COALESCE\(c\.invoice_amount_30d, 0\) AS current_amount, "
    r"COALESCE\(p\.invoice_amount_30d, 0\) AS previous_amount, "
    r"CASE WHEN COALESCE\(p\.invoice_amount_30d, 0\) = 0 THEN NULL "
    r"ELSE 1\.0 \* \(COALESCE\(c\.invoice_amount_30d, 0\) - COALESCE\(p\.invoice_amount_30d, 0\)\) "
    r"/ COALESCE\(p\.invoice_amount_30d, 0\) END AS `invoice_amount_growth_30d` "
    r"FROM current_period c FULL OUTER JOIN previous_period p ON c\.entity_id <=> p\.entity_id;?",
    re.I,
)


class DerivedSQLValidator:
    def validate(
        self, code: str, language: str, metric: DerivedMetricIR, context: ExecutionContext
    ) -> SQLValidationResult:
        errors = []
        for dependency in metric.dependencies:
            if dependency.metric.joins or dependency.metric.column_filters:
                errors.append("A derived metric dependency must be single-source")
        if language != "spark_sql" or FORBIDDEN.search(code):
            errors.append("Incorrect language or forbidden SQL statement")
        match = CTES.fullmatch(code.strip())
        if match is None or TAIL.fullmatch(" ".join(match["select"].split())) is None:
            errors.append("SQL is outside the supported growth-rate CTE grammar")
        else:
            plan = DerivedMetricPlanner().plan(metric, context)
            for base in plan.base_metrics:
                report = SQLValidator().validate(
                    match[base.role], language, base.metric_ir, base.execution_context
                )
                errors.extend(f"{base.role}: {error}" for error in report.errors)
        return SQLValidationResult(
            valid=not errors,
            errors=errors,
            warnings=["仅静态校验；未验证 Spark 类型、数据或执行结果。"],
        )

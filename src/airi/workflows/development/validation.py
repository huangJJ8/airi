import re
from datetime import timedelta
from typing import Self

from pydantic import Field, model_validator

from airi.core.execution import SHANGHAI, ExecutionContext
from airi.core.schemas import StrictSchema
from airi.metric_ir.models import MetricIR


class SQLValidationResult(StrictSchema):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.valid != (not self.errors):
            raise ValueError("valid must agree with the absence of errors")
        return self


# Deliberately a closed grammar, NOT a general SQL parser or substring security check.
# Accept only the supported projection, qualified source, two DATE bounds and one group key.
# Full matching rejects comments, nested queries, UDFs, OR, UNION, extra statements and clauses.
IDENT = r"(?:`[a-z][a-z0-9_]{0,63}`|[a-z][a-z0-9_]{0,63})"
SELECT_GRAMMAR = re.compile(
    rf"\s*SELECT\s+(?P<entity>{IDENT})\s+AS\s+entity_id\s*,\s*"
    rf"(?P<aggregation>SUM|COUNT)\s*\(\s*(?P<amount>{IDENT}|\*)\s*\)\s+AS\s+(?P<alias>{IDENT})\s+"
    rf"FROM\s+(?P<source>{IDENT}\s*\.\s*{IDENT}(?:\s*\.\s*{IDENT})?)\s+"
    rf"WHERE\s+(?P<lower_field>{IDENT})\s*>=\s*DATE\s*'(?P<start>\d{{4}}-\d{{2}}-\d{{2}})'\s+"
    rf"AND\s+(?P<upper_field>{IDENT})\s*<\s*DATE\s*'(?P<end>\d{{4}}-\d{{2}}-\d{{2}})'\s+"
    rf"GROUP\s+BY\s+(?P<group>{IDENT})\s*;?\s*",
    re.IGNORECASE,
)
FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|MERGE)\b", re.I)


def normalize_identifier(value: str) -> str:
    return re.sub(r"\s|`", "", value).lower()


class SQLValidator:
    def validate(
        self, code: str, language: str, metric: MetricIR, context: ExecutionContext
    ) -> SQLValidationResult:
        errors = []
        if getattr(metric, "joins", None):
            raise ValueError("A metric declaring joins must be validated by JoinSQLValidator")
        if not code.strip():
            errors.append("SQL must not be empty")
        if language != "spark_sql":
            errors.append("Language must be spark_sql")
        if FORBIDDEN.search(code):
            errors.append("Forbidden SQL statement")
        if not re.match(r"\s*SELECT\b", code, re.I):
            errors.append("Only SELECT is allowed")
        if not re.search(r"\bGROUP\s+BY\b", code, re.I):
            errors.append("GROUP BY is required")
        if not re.search(r"\bWHERE\b", code, re.I) or ">=" not in code or "<" not in code:
            errors.append("Both half-open window conditions are required")
        match = SELECT_GRAMMAR.fullmatch(code)
        if match is None:
            errors.append("SQL is outside the supported single SELECT grammar")
        else:
            fields = match.groupdict()
            source_parts = [metric.source.catalog, metric.source.database, metric.source.table]
            expected_source = ".".join(part for part in source_parts if part is not None)
            for actual, expected, message in (
                (fields["source"], expected_source, "Source table differs from Metric IR"),
                (fields["entity"], metric.entity_key, "Entity projection differs from Metric IR"),
                (fields["group"], metric.entity_key, "GROUP BY differs from Metric IR"),
                (
                    fields["amount"],
                    metric.aggregation.field if metric.aggregation.function == "sum" else "*",
                    "Aggregation field differs from Metric IR",
                ),
                (fields["alias"], metric.name, "Metric alias differs from Metric IR"),
            ):
                if expected is None or normalize_identifier(actual) != expected:
                    errors.append(message)
            window = metric.window
            if fields["aggregation"].lower() != metric.aggregation.function:
                errors.append("Aggregation operation differs from Metric IR")
            if window is None or window.unit != "day" or window.timezone != context.timezone:
                errors.append("Unsupported or mismatched Metric IR window")
            else:
                end = context.anchor_time.astimezone(SHANGHAI).date()
                start = end - timedelta(days=window.size)
                if fields["start"] != start.isoformat() or fields["end"] != end.isoformat():
                    errors.append("Window bounds differ from anchor and Metric IR")
                if any(
                    normalize_identifier(fields[key]) != window.time_field
                    for key in ("lower_field", "upper_field")
                ):
                    errors.append("Window time field differs from Metric IR")
        if (
            metric.aggregation.function not in {"sum", "count"}
            or metric.filters
            or metric.dimensions
        ):
            errors.append("Metric IR contains semantics not supported by this SELECT grammar")
        return SQLValidationResult(
            valid=not errors,
            errors=errors,
            warnings=["仅静态校验；未验证源表、字段类型、数据质量或 Spark 执行结果。"],
        )

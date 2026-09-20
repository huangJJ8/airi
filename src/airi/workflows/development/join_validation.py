"""Static validation for the joined-metric SQL shape.

Same policy as the single-source validator: a closed grammar that is re-parsed and
compared field by field against the Metric IR, never a substring security check.
The difference is that this grammar admits a bounded number of anchored JOIN
clauses, so structure — alias, source, key direction, exclusion direction — is
checked rather than trusted.
"""

import re
from typing import NamedTuple

from airi.core.execution import ExecutionContext
from airi.metric_ir.models import MetricIR
from airi.workflows.development.validation import (
    FORBIDDEN,
    SQLValidationResult,
    normalize_identifier,
)

IDENT = r"(?:`[a-z][a-z0-9_]{0,63}`|[a-z][a-z0-9_]{0,63})"
QUALIFIED = rf"{IDENT}\s*\.\s*{IDENT}"
SOURCE = rf"(?:{IDENT}\s*\.\s*)?{IDENT}\s*\.\s*{IDENT}"
COMPARISON = rf"{QUALIFIED}\s*[=<>]{{1,2}}\s*{QUALIFIED}"

HEADER = (
    rf"\s*SELECT\s+(?P<entity>{QUALIFIED})\s+AS\s+entity_id\s*,\s*"
    rf"COUNT\s*\(\s*DISTINCT\s+(?P<value>{QUALIFIED})\s*\)\s+AS\s+(?P<alias>{IDENT})\s+"
)
FROM_CLAUSE = rf"FROM\s+(?P<source>{SOURCE})\s+AS\s+(?P<alias>{IDENT})\s+"
JOIN_CLAUSE = (
    rf"(?P<join_type>INNER|LEFT)\s+JOIN\s+(?P<source>{SOURCE})\s+AS\s+(?P<alias>{IDENT})\s+"
    rf"ON\s+(?P<conditions>{QUALIFIED}\s*=\s*{QUALIFIED}"
    rf"(?:\s+AND\s+{QUALIFIED}\s*=\s*{QUALIFIED})*)\s+"
)
WHERE_CLAUSE = rf"WHERE\s+(?P<filters>{COMPARISON}(?:\s+AND\s+{COMPARISON})*)\s+"
GROUP_CLAUSE = rf"GROUP\s+BY\s+(?P<group>{QUALIFIED})\s*$"

Comparison = tuple[str, str, str, str, str]


class JoinClause(NamedTuple):
    join_type: str
    source: str
    alias: str
    conditions: frozenset[Comparison]


class JoinSQLShape(NamedTuple):
    entity: str
    value: str
    metric_alias: str
    source: str
    base_alias: str
    joins: tuple[JoinClause, ...]
    exclusions: frozenset[Comparison]
    group: str


class _Cursor:
    """Sequential matcher: each clause consumes exactly the text it owns."""

    def __init__(self, text: str):
        self.text, self.pos = text, 0

    def take(self, pattern: str) -> dict | None:
        match = re.compile(pattern, re.IGNORECASE | re.VERBOSE).match(self.text, self.pos)
        if match is None:
            return None
        self.pos = match.end()
        return match.groupdict()

    @property
    def exhausted(self) -> bool:
        return self.pos >= len(self.text)


def _qualified(text: str) -> tuple[str, str]:
    alias, _, field = normalize_identifier(text).partition(".")
    return alias, field


def _source(text: str) -> str:
    return ".".join(normalize_identifier(part) for part in text.split("."))


def _declared_source(catalog: str | None, database: str, table: str) -> str:
    return ".".join(part for part in (catalog, database, table) if part is not None)


def _comparisons(text: str) -> frozenset[Comparison]:
    pairs = set()
    for chunk in re.split(r"\s+AND\s+", text, flags=re.IGNORECASE):
        match = re.fullmatch(
            rf"(?P<left>{QUALIFIED})\s*(?P<operator>[=<>]{{1,2}})\s*(?P<right>{QUALIFIED})",
            chunk.strip(),
            re.IGNORECASE | re.VERBOSE,
        )
        if match is None:
            continue
        pairs.add(
            (
                *_qualified(match.group("left")),
                match.group("operator"),
                *_qualified(match.group("right")),
            )
        )
    return frozenset(pairs)


def _declared_comparisons(comparisons) -> frozenset[Comparison]:
    return frozenset(
        (
            item.left.alias,
            item.left.field,
            item.operator,
            item.right.alias,
            item.right.field,
        )
        for item in comparisons
    )


def parse_join_sql(body: str) -> JoinSQLShape | None:
    """Return the parsed structure, or None when the text is outside the grammar."""
    cursor = _Cursor(body)
    header = cursor.take(HEADER)
    source = cursor.take(FROM_CLAUSE)
    joins = []
    while (clause := cursor.take(JOIN_CLAUSE)) is not None:
        joins.append(clause)
    where = cursor.take(WHERE_CLAUSE)
    group = cursor.take(GROUP_CLAUSE)
    if None in (header, source, where, group) or not cursor.exhausted:
        return None
    return JoinSQLShape(
        entity=header["entity"],
        value=header["value"],
        metric_alias=header["alias"],
        source=source["source"],
        base_alias=source["alias"],
        joins=tuple(
            JoinClause(
                join_type=clause["join_type"].upper(),
                source=clause["source"],
                alias=clause["alias"],
                conditions=_comparisons(clause["conditions"]),
            )
            for clause in joins
        ),
        exclusions=_comparisons(where["filters"]),
        group=group["group"],
    )


class JoinSQLValidator:
    """Re-parse the rendered join draft and compare it to the Metric IR."""

    def validate(
        self, code: str, language: str, metric: MetricIR, context: ExecutionContext
    ) -> SQLValidationResult:
        errors: list[str] = []
        if not code.strip():
            errors.append("SQL must not be empty")
        if language != "spark_sql":
            errors.append("Language must be spark_sql")
        if FORBIDDEN.search(code):
            errors.append("Forbidden SQL statement")
        if re.search(r"\bDATE\s*'", code, re.IGNORECASE):
            errors.append("The join mechanism carries no time window")
        body = code.strip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        if ";" in body:
            errors.append("Only a single statement is allowed")
        shape = parse_join_sql(body)
        if shape is None:
            errors.append("SQL is outside the supported joined SELECT grammar")
            return SQLValidationResult(valid=False, errors=errors, warnings=[WARNING])
        errors.extend(self._differences(shape, metric))
        return SQLValidationResult(valid=not errors, errors=errors, warnings=[WARNING])

    @staticmethod
    def _differences(shape: JoinSQLShape, metric: MetricIR) -> list[str]:
        errors: list[str] = []
        expected_base = _declared_source(
            metric.source.catalog, metric.source.database, metric.source.table
        )
        entity_alias, entity_field = _qualified(shape.entity)
        value_alias, value_field = _qualified(shape.value)
        group_alias, group_field = _qualified(shape.group)
        for actual, expected, message in (
            (_source(shape.source), expected_base, "Base source differs from Metric IR"),
            (shape.base_alias, metric.source_alias, "Base alias differs from Metric IR"),
            (shape.metric_alias, metric.name, "Metric alias differs from Metric IR"),
            (entity_alias, metric.source_alias, "Entity projection is not on the base alias"),
            (entity_field, metric.entity_key, "Entity projection differs from Metric IR"),
            (group_alias, metric.source_alias, "GROUP BY is not on the base alias"),
            (group_field, metric.entity_key, "GROUP BY differs from Metric IR"),
            (value_alias, metric.aggregation_alias, "Aggregated alias differs from Metric IR"),
            (value_field, metric.aggregation.field, "Aggregated field differs from Metric IR"),
        ):
            if normalize_identifier(str(actual)) != normalize_identifier(str(expected)):
                errors.append(message)
        if len(shape.joins) != len(metric.joins):
            errors.append("Rendered join count differs from Metric IR")
        for clause, join in zip(shape.joins, metric.joins, strict=False):
            prefix = f"Rendered join {join.alias}"
            if (
                clause.alias != join.alias
                or clause.join_type != join.join_type.upper()
                or _source(clause.source)
                != _declared_source(join.source.catalog, join.source.database, join.source.table)
            ):
                errors.append(f"{prefix} source or type differs from Metric IR")
            if clause.conditions != _declared_comparisons(join.conditions):
                errors.append(f"{prefix} keys differ from Metric IR")
        if shape.exclusions != _declared_comparisons(metric.column_filters):
            errors.append("Rendered exclusions differ from Metric IR")
        return errors


WARNING = "仅静态校验；未验证关系表覆盖率、自然人身份合并或 Spark 执行结果。"

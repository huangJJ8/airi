from typing import Literal, Self

from pydantic import Field, model_validator

from airi.core.schemas import Identifier, NonEmptyText, StrictSchema

Scalar = str | int | float | bool


class DataSource(StrictSchema):
    catalog: Identifier | None = None
    database: Identifier
    table: Identifier


class TimeWindow(StrictSchema):
    """Trailing window: [anchor - size * unit, anchor), in the declared timezone."""

    size: int = Field(gt=0, le=3660)
    unit: Literal["hour", "day", "month"]
    time_field: Identifier
    timezone: Literal["UTC", "Asia/Shanghai"] = "UTC"


class FilterCondition(StrictSchema):
    field: Identifier
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "not_null"]
    value: Scalar | list[Scalar] | None = None

    @model_validator(mode="after")
    def validate_operand(self) -> Self:
        if self.operator in {"is_null", "not_null"}:
            if self.value is not None:
                raise ValueError("Null operators must not carry a value")
        elif self.operator in {"in", "not_in"}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("Set operators require a non-empty list")
            if len({type(item) for item in self.value}) != 1:
                raise ValueError("Set operands must have the same scalar type")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError("Comparison operators require a scalar value")
        return self


class Aggregation(StrictSchema):
    function: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: Identifier | None = None

    @model_validator(mode="after")
    def require_field(self) -> Self:
        if self.function != "count" and self.field is None:
            raise ValueError("Only count supports an omitted field (row count)")
        return self


class QualifiedField(StrictSchema):
    """A column reference that is unambiguous because it names its source alias."""

    alias: Identifier
    field: Identifier


class FieldComparison(StrictSchema):
    """Column-to-column predicate. Never a column-to-literal filter: no values here.

    v1 policy: join keys use equality only; cross-column predicates use inequality
    (for example excluding an entity that the relationship path loops back to).
    """

    left: QualifiedField
    operator: Literal["=", "<>"] = "="
    right: QualifiedField


class JoinSpec(StrictSchema):
    """One constrained, structured join. No free-form SQL, no nested queries."""

    alias: Identifier
    join_type: Literal["inner", "left"] = "inner"
    source: DataSource
    conditions: list[FieldComparison] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def equality_join_keys_only(self) -> Self:
        if any(condition.operator != "=" for condition in self.conditions):
            raise ValueError("Join conditions support the equality operator only")
        return self


class MetricIR(StrictSchema):
    """Phase 1: one source, one aggregation, AND filters and optional trailing window.

    Phase 11 adds an optional, structured join path: ``joins`` is empty for every
    single-source metric, so existing metrics keep their exact semantics. Joins
    are declared as data (source, key comparison, join type) and are never
    generated as SQL text by the caller.
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    name: Identifier
    display_name: NonEmptyText | None = None
    description: NonEmptyText
    entity_type: Literal["enterprise"] | None = None
    entity_key: Identifier
    partition_field: Identifier | None = None
    source: DataSource
    aggregation: Aggregation
    dimensions: list[Identifier] = Field(default_factory=list, max_length=20)
    filters: list[FilterCondition] = Field(default_factory=list, max_length=100)
    window: TimeWindow | None = None
    # Phase 11 join path. All four are inert unless `joins` is nonempty.
    source_alias: Identifier | None = None
    aggregation_alias: Identifier | None = None
    joins: list[JoinSpec] = Field(default_factory=list, max_length=2)
    column_filters: list[FieldComparison] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_dimensions(self) -> Self:
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("Dimensions must be unique")
        if self.entity_key in self.dimensions:
            raise ValueError("Entity key is implicit in the grain; do not repeat it")
        return self

    @model_validator(mode="after")
    def consistent_join_declaration(self) -> Self:
        if not self.joins:
            if self.source_alias is not None or self.aggregation_alias is not None:
                raise ValueError("Aliases are only meaningful when joins are declared")
            if self.column_filters:
                raise ValueError("Column predicates are only meaningful when joins are declared")
            return self
        if self.source_alias is None or self.aggregation_alias is None:
            raise ValueError("Joined metrics must alias both the base source and the aggregate")
        aliases = [self.source_alias, *(join.alias for join in self.joins)]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Join aliases must be unique")
        for join in self.joins:
            if join.alias == self.source_alias:
                raise ValueError("A joined source cannot reuse the base alias")
        for comparison in [c for join in self.joins for c in join.conditions] + self.column_filters:
            for side in (comparison.left, comparison.right):
                if side.alias not in aliases:
                    raise ValueError("Every predicate field must reference a declared alias")
        if self.aggregation_alias not in aliases:
            raise ValueError("Aggregation alias must reference a declared alias")
        return self

    def predicate_aliases(self) -> set[str]:
        fields = [c for join in self.joins for c in join.conditions] + self.column_filters
        return {side.alias for comparison in fields for side in (comparison.left, comparison.right)}

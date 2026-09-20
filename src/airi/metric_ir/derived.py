from typing import Literal, Self

from pydantic import Field, model_validator

from airi.core.exceptions import (
    DerivedDependencyError,
    DerivedMetricError,
    ZeroDivisionStrategyError,
)
from airi.core.schemas import Identifier, NonEmptyText, StrictSchema
from airi.metric_ir.invoice import validate_invoice_metric
from airi.metric_ir.models import MetricIR


class AtomicDependency(StrictSchema):
    role: Literal["current", "previous"]
    metric: MetricIR
    anchor_offset_days: int = Field(ge=0, le=30)


class GrowthExpression(StrictSchema):
    operator: Literal["growth_rate"]


class ZeroDivision(StrictSchema):
    strategy: Literal["null"]


class DerivedMetricIR(StrictSchema):
    schema_version: Literal["1.0.0"] = "1.0.0"
    metric_type: Literal["derived"] = "derived"
    name: Identifier
    display_name: NonEmptyText
    description: NonEmptyText
    dependencies: list[AtomicDependency] = Field(min_length=2, max_length=2)
    expression: GrowthExpression
    zero_division: ZeroDivision

    @model_validator(mode="after")
    def distinct_roles(self) -> Self:
        if {item.role for item in self.dependencies} != {"current", "previous"}:
            raise ValueError("Exactly one current and one previous dependency required")
        return self


def validate_derived_metric(metric: DerivedMetricIR) -> DerivedMetricIR:
    if metric.expression.operator != "growth_rate" or metric.name != "invoice_amount_growth_30d":
        raise DerivedMetricError("Only enterprise invoice amount growth_rate is supported")
    if metric.zero_division.strategy != "null":
        raise ZeroDivisionStrategyError("Only NULL on a zero denominator is supported")
    for dependency in metric.dependencies:
        try:
            validate_invoice_metric(dependency.metric)
        except Exception as exc:
            raise DerivedDependencyError(
                "Dependency must be the supported invoice amount metric"
            ) from exc
        expected = 0 if dependency.role == "current" else 30
        if (
            dependency.metric.aggregation.function != "sum"
            or dependency.anchor_offset_days != expected
        ):
            raise DerivedDependencyError(
                "Growth requires current SUM and previous SUM offset by 30 days"
            )
    if metric.dependencies[0].metric != metric.dependencies[1].metric:
        raise DerivedDependencyError("Both periods must use identical atomic metric definitions")
    return metric

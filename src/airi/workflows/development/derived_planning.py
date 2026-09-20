from datetime import timedelta
from typing import Literal

from pydantic import Field

from airi.core.execution import ExecutionContext
from airi.core.schemas import StrictSchema, VersionedReference
from airi.metric_ir.derived import DerivedMetricIR, validate_derived_metric
from airi.metric_ir.models import MetricIR


class AtomicMetricPlan(StrictSchema):
    role: Literal["current", "previous"]
    metric_ir: MetricIR
    execution_context: ExecutionContext


class DerivedMetricPlan(StrictSchema):
    base_metrics: list[AtomicMetricPlan] = Field(min_length=2, max_length=2)
    operator: Literal["growth_rate"] = "growth_rate"
    tool: VersionedReference


class DerivedMetricPlanner:
    def plan(self, metric: DerivedMetricIR, context: ExecutionContext) -> DerivedMetricPlan:
        validate_derived_metric(metric)
        plans = []
        for role in ("current", "previous"):
            dependency = next(item for item in metric.dependencies if item.role == role)
            shifted = ExecutionContext(
                anchor_time=context.anchor_time - timedelta(days=dependency.anchor_offset_days),
                timezone=context.timezone,
            )
            plans.append(
                AtomicMetricPlan(role=role, metric_ir=dependency.metric, execution_context=shifted)
            )
        return DerivedMetricPlan(
            base_metrics=plans,
            tool=VersionedReference(name="generate_growth_rate_sql", version="1.0.0"),
        )

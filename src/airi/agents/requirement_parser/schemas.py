from typing import Self

from pydantic import model_validator

from airi.core.schemas import NonEmptyText, StrictSchema
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR


class RequirementParseOutput(StrictSchema):
    metric_ir: MetricIR | DerivedMetricIR | None
    unsupported_reason: NonEmptyText | None

    @model_validator(mode="after")
    def exclusive_result(self) -> Self:
        if (self.metric_ir is None) == (self.unsupported_reason is None):
            raise ValueError("Return either a metric_ir or an unsupported_reason")
        return self

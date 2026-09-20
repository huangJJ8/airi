from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from airi.approvals.artifacts import DevelopmentArtifact
from airi.core.execution import ExecutionContext
from airi.core.schemas import NonEmptyText, StrictSchema, Version
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR
from airi.workflows.development.derived_planning import DerivedMetricPlan
from airi.workflows.development.planning import SkillPlan, ToolPlan
from airi.workflows.development.validation import SQLValidationResult


class DevelopmentRequest(StrictSchema):
    requirement: NonEmptyText
    # Closed list, not a free string: an unknown scenario has no declaration and
    # must fail at the API boundary rather than resolve to some default later.
    scenario: Literal["invoice_risk", "enterprise_relation"]
    execution_context: ExecutionContext


class DevelopmentResult(StrictSchema):
    workflow_run_id: str = Field(default_factory=lambda: str(uuid4()))
    metric_ir: MetricIR | DerivedMetricIR
    skill_plan: SkillPlan
    tool_plan: ToolPlan
    artifact: DevelopmentArtifact
    derived_plan: DerivedMetricPlan | None = None
    validation: SQLValidationResult
    execution_context: ExecutionContext
    prompt_version: Version
    requires_human_review: Literal[True] = True

    @field_validator("requires_human_review", mode="before")
    @classmethod
    def require_review(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("Human review cannot be disabled")
        return True

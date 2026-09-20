from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from airi.core.schemas import NonEmptyText, StrictSchema, Version


class ApprovalRecord(StrictSchema):
    approval_id: str
    workflow_run_id: str
    artifact_id: str
    artifact_version: Version
    metric_name: str
    metric_ir_hash: str
    artifact_hash: str
    reviewer: str | None
    decision: Literal["pending", "approved", "rejected"]
    comment: str | None
    created_at: datetime
    reviewed_at: datetime | None

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        if self.decision == "pending":
            if any(value is not None for value in (self.reviewer, self.comment, self.reviewed_at)):
                raise ValueError("Pending review cannot have a decision author or time")
        elif not self.reviewer or not self.comment or self.reviewed_at is None:
            raise ValueError("A decision requires reviewer, comment and reviewed_at")
        return self


class SubmitReview(StrictSchema):
    artifact_id: str = Field(min_length=1, max_length=36)
    metric_ir_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewDecision(StrictSchema):
    reviewer: NonEmptyText = Field(max_length=128)
    comment: NonEmptyText

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from airi.core.schemas import Identifier, StrictSchema
from airi.evaluation.models import EvaluationPolicy, MetricEvaluation
from airi.execution.models import ExecutionPlan


class Times(StrictSchema):
    @field_validator("*", mode="before")
    @classmethod
    def dates(cls, value, info):
        if info.field_name in {
            "anchor_time",
            "observation_time",
            "snapshot_time",
            "start",
            "end",
        } and isinstance(value, str):
            if "T" not in value:
                raise ValueError("Explicit ISO datetime required")
            return datetime.fromisoformat(value)
        return value


class DatasetSource(StrictSchema):
    database: Literal["tmp_db"] = "tmp_db"
    table: Literal["invoice_risk_sample", "enterprise_relation_sample"] = "invoice_risk_sample"


class SnapshotPartition(StrictSchema):
    field: Literal["dt"] = "dt"
    value: str = Field(pattern=r"^\d{8}$")


class DatasetSnapshot(Times):
    dataset_snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    name: Identifier
    source: DatasetSource
    # The scenario's declared entity field. The snapshot must say which identifier
    # its rows are keyed by, because the metric result is joined on that same key.
    entity_key: Literal["seller_tax_no", "enterprise_id"] = "seller_tax_no"
    snapshot_time: AwareDatetime
    partition: SnapshotPartition
    row_count: int = Field(ge=0, le=1000)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class LabelDefinition(StrictSchema):
    label_definition_id: str = Field(default_factory=lambda: str(uuid4()))
    name: Identifier
    entity_key: Literal["seller_tax_no", "enterprise_id"] = "seller_tax_no"
    label_field: Literal["bad_flag"] = "bad_flag"
    bad_value: int = Field(default=1, ge=0, le=1)
    good_value: int = Field(default=0, ge=0, le=1)
    unknown_strategy: Literal["exclude"] = "exclude"

    @model_validator(mode="after")
    def distinct(self):
        if self.bad_value == self.good_value:
            raise ValueError("Good and bad labels must differ")
        return self


class LabelWindow(Times):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Label window must have positive duration")
        return self


class ExperimentMetric(StrictSchema):
    artifact_id: str
    artifact_version: Literal["1.0.0"] = "1.0.0"


class ExperimentSpec(Times):
    experiment_spec_id: str = Field(default_factory=lambda: str(uuid4()))
    experiment_name: Identifier
    metric: ExperimentMetric
    approval_id: str
    test_run_id: str
    dataset_snapshot_id: str
    label_definition_id: str
    environment_validation_run_id: str | None = None
    anchor_time: AwareDatetime
    observation_time: AwareDatetime
    label_window: LabelWindow
    evaluation: EvaluationPolicy = EvaluationPolicy()


class ExperimentExecutionPlan(ExecutionPlan):
    dataset_snapshot_id: str
    label_definition_id: str
    execution_mode: Literal["mock", "spark_test"]


class ExperimentRun(StrictSchema):
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    experiment_run_id: str = Field(default_factory=lambda: str(uuid4()))
    experiment_spec_id: str
    artifact_id: str
    dataset_snapshot_id: str
    label_definition_id: str
    status: Literal["pending", "running", "completed", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    metric_row_count: int = 0
    labeled_row_count: int = 0
    failure_category: str | None = None


class ExperimentReport(StrictSchema):
    run: ExperimentRun
    execution_mode: Literal["mock", "spark_test"]
    evaluation: MetricEvaluation | None = None
    join_summary: dict[str, int] = Field(default_factory=dict)
    reproducibility: dict = Field(default_factory=dict)
    leakage_check: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: Literal[True] = True

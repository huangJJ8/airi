from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from airi.core.execution import ExecutionContext
from airi.core.schemas import NonEmptyText, StrictSchema
from airi.evaluation.models import EvaluationPolicy, ThresholdCandidate
from airi.experiments.models import LabelWindow, Times
from airi.refinement.models import MetricComparisonResult


def new_id():
    return str(uuid4())


class TimeSlice(Times):
    time_slice_id: str = Field(default_factory=new_id)
    name: NonEmptyText
    dataset_snapshot_id: NonEmptyText
    observation_time: AwareDatetime
    label_window: LabelWindow
    role: Literal["historical", "oot"]

    @model_validator(mode="after")
    def valid_time(self):
        ExecutionContext(anchor_time=self.observation_time)
        if self.observation_time >= self.label_window.start:
            raise ValueError("temporal_validation_invalid: label leakage")
        return self


class TemporalSeriesInput(StrictSchema):
    name: NonEmptyText
    label_definition_id: NonEmptyText
    scenario: Literal["invoice_risk"] = "invoice_risk"
    slices: list[TimeSlice] = Field(min_length=4, max_length=24)
    reference_slice_id: NonEmptyText

    @model_validator(mode="after")
    def valid_series(self):
        historical = [s for s in self.slices if s.role == "historical"]
        oot = [s for s in self.slices if s.role == "oot"]
        if len(historical) < 3 or len(oot) != 1:
            raise ValueError("insufficient_temporal_slices")
        for field in ("time_slice_id", "dataset_snapshot_id", "observation_time"):
            if len({getattr(s, field) for s in self.slices}) != len(self.slices):
                raise ValueError("temporal_series_invalid: duplicate slice")
        if self.reference_slice_id not in {s.time_slice_id for s in historical}:
            raise ValueError("temporal_series_invalid: historical reference required")
        reference = next(s for s in historical if s.time_slice_id == self.reference_slice_id)
        if reference.observation_time != min(s.observation_time for s in historical):
            raise ValueError("Reference must be earliest historical slice")
        if oot[0].observation_time <= max(s.observation_time for s in historical):
            raise ValueError("OOT must follow every historical observation")
        return self


class TemporalDatasetSeries(TemporalSeriesInput):
    series_id: str = Field(default_factory=new_id)
    snapshot_hashes: dict[str, str]
    label_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StabilityPolicy(StrictSchema):
    version: Literal["1.0.0"] = "1.0.0"
    psi_review_above: float = Field(default=0.2, ge=0)
    ks_material_drop: float = Field(default=0.05, ge=0, le=1)
    coverage_material_drop: float = Field(default=0.05, ge=0, le=1)
    iv_material_change: float = Field(default=1.0, ge=0)
    threshold_precision_drop: float = Field(default=0.1, ge=0, le=1)
    threshold_recall_drop: float = Field(default=0.1, ge=0, le=1)
    threshold_lift_drop: float = Field(default=0.5, ge=0)
    direction_flip_allowed: Literal[False] = False
    oot_required: Literal[True] = True
    min_historical_slices: int = Field(default=3, ge=3)
    psi_epsilon: float = Field(default=1e-6, gt=0, le=0.01)
    advisory_only: Literal[True] = True


class PromotionPolicy(StrictSchema):
    version: Literal["1.0.0"] = "1.0.0"
    min_not_materially_worse_fraction: float = Field(default=0.75, gt=0.5, le=1)
    synthetic_review_only: Literal[True] = True
    research_governance_only: Literal[True] = True


class TemporalValidationRequest(StrictSchema):
    source_refinement_run_id: NonEmptyText
    series_id: NonEmptyText


class SliceArtifact(StrictSchema):
    time_slice_id: str
    role: Literal["baseline", "candidate"]
    artifact_id: str
    content_hash: str
    metric_ir_hash: str


class TemporalValidationSpec(TemporalValidationRequest):
    temporal_validation_run_id: str = Field(default_factory=new_id)
    baseline_artifact_id: str
    candidate_artifact_id: str
    historical_slice_ids: list[str]
    oot_slice_id: str
    reference_slice_id: str
    evaluation_policy: EvaluationPolicy
    stability_policy: StabilityPolicy = StabilityPolicy()
    promotion_policy: PromotionPolicy = PromotionPolicy()
    slice_artifacts: list[SliceArtifact]
    input_hash: str
    provenance: dict


class PSIBin(StrictSchema):
    bucket: int
    is_null: bool
    expected_count: int
    actual_count: int
    expected_pct: float
    actual_pct: float
    contribution: float


class PSIResult(StrictSchema):
    status: Literal["available", "not_available"]
    value: float | None
    boundaries: list[Decimal]
    epsilon: float
    bins: list[PSIBin]
    warnings: list[str]


class ThresholdTemporalResult(StrictSchema):
    performance: ThresholdCandidate
    deltas: dict[str, float | None]


Eligibility = Literal["eligible_for_review", "not_eligible", "need_more_evidence"]


class TemporalMetricResult(StrictSchema):
    artifact_id: str
    artifact_hash: str
    experiment_run_id: str
    experiment_report_hash: str
    test_run_id: str
    approval_id: str
    environment: dict
    coverage: float | None
    ks: float | None
    iv: float | None
    direction: Literal["higher_is_riskier", "lower_is_riskier", "undetermined"]
    labeled_sample: int = Field(ge=0)
    warnings: list[str]
    psi: PSIResult
    frozen_thresholds: list[ThresholdTemporalResult]


class TemporalSliceResult(StrictSchema):
    time_slice_id: str
    name: str
    role: Literal["historical", "oot"]
    dataset_snapshot_id: str
    baseline: TemporalMetricResult
    candidate: TemporalMetricResult
    comparison: MetricComparisonResult


class OOTValidationResult(TemporalSliceResult):
    role: Literal["oot"] = "oot"


class TemporalValidationReport(StrictSchema):
    spec: TemporalValidationSpec
    status: Literal[
        "pending_sql_review", "running", "passed", "passed_with_warnings", "failed", "inconclusive"
    ]
    historical: list[TemporalSliceResult] = Field(default_factory=list)
    oot: OOTValidationResult | None = None
    stability: dict = Field(default_factory=dict)
    threshold_transferability: dict = Field(default_factory=dict)
    diagnostics: list[dict] = Field(default_factory=list)
    improvement_pattern: dict[str, int] = Field(default_factory=dict)
    promotion_eligibility: Eligibility = "need_more_evidence"
    synthetic_data: bool
    warnings: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    failure_category: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    requires_human_review: Literal[True] = True


class PromotionReviewRequest(StrictSchema):
    temporal_validation_run_id: NonEmptyText


class PromotionDecision(StrictSchema):
    decision: Literal["approved_for_versioning", "rejected", "need_more_evidence"]
    reviewer: NonEmptyText
    comment: NonEmptyText


class PromotionEvidencePackage(StrictSchema):
    refinement: dict
    linked_evidence: dict
    temporal_validation: TemporalValidationReport
    source_hashes: dict[str, str]
    missing_evidence: list[str]
    research_governance_only: Literal[True] = True
    production_deployed: Literal[False] = False


class PromotionReview(StrictSchema):
    promotion_review_id: str = Field(default_factory=new_id)
    temporal_validation_run_id: str
    candidate_artifact_id: str
    decision: Literal["pending", "approved_for_versioning", "rejected", "need_more_evidence"] = (
        "pending"
    )
    reviewer: str | None = None
    comment: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence: PromotionEvidencePackage
    evidence_hash: str
    production_deployed: Literal[False] = False

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from airi.core.schemas import NonEmptyText, StrictSchema
from airi.evaluation.models import MetricEvaluation
from airi.metric_ir.models import MetricIR
from airi.reflection.models import RefinementProposal

Outcome = Literal["improved", "worse", "mixed", "inconclusive"]
RefinementOutcome = Outcome


class ProposalDecisionInput(StrictSchema):
    decision: Literal["pending", "accepted_for_investigation", "rejected", "need_more_evidence"]
    reviewer: NonEmptyText
    comment: NonEmptyText


class ProposalDecision(ProposalDecisionInput):
    previous_decisions: list[dict] = Field(default_factory=list)
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    reflection_run_id: str
    proposal_id: str
    proposal_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposalView(StrictSchema):
    proposal_id: str
    proposal: RefinementProposal
    automation_capability: Literal["supported", "manual_only"]
    allowed_windows: list[int]
    decision: ProposalDecision | None = None


class ParameterSelection(StrictSchema):
    window_days: int = Field(gt=0, le=90)


class RefinementRequest(StrictSchema):
    reflection_run_id: str
    proposal_id: str
    parameter_selection: ParameterSelection


class ChangedField(StrictSchema):
    field: str
    before: str | int | None
    after: str | int | None


class MetricSemanticDiff(StrictSchema):
    changed_fields: list[ChangedField]
    unchanged: list[str] = [
        "source",
        "entity_key",
        "entity_type",
        "aggregation",
        "filters",
        "dimensions",
        "partition_field",
        "window.time_field",
        "window.unit",
        "window.timezone",
    ]


class CandidateMetricDefinition(StrictSchema):
    candidate_metric_id: str = Field(default_factory=lambda: str(uuid4()))
    baseline_artifact_id: str
    baseline_experiment_run_id: str
    reflection_run_id: str
    proposal_id: str
    metric_ir: MetricIR
    metric_ir_hash: str
    semantic_diff: MetricSemanticDiff


class CandidateArtifact(StrictSchema):
    artifact_id: str
    artifact_version: str
    content_hash: str
    metric_ir_hash: str
    baseline_artifact_id: str
    refinement_run_id: str
    proposal_id: str
    role: Literal["candidate_draft"] = "candidate_draft"


class RefinementComparisonPolicy(StrictSchema):
    version: Literal["1.0.0"] = "1.0.0"
    ks_material_delta: float = Field(default=0.02, ge=0, le=1)
    coverage_material_delta: float = Field(default=0.02, ge=0, le=1)
    min_labeled_samples: int = Field(default=100, ge=1)
    max_experiment_warnings: int = Field(default=10, ge=0)


class MetricComparisonResult(StrictSchema):
    baseline: MetricEvaluation
    candidate: MetricEvaluation
    coverage_delta: float | None
    ks_delta: float | None
    iv_delta: float | None
    threshold_matches: list[dict]
    outcome: Outcome
    reasons: list[str]


class RefinementPlan(StrictSchema):
    request: RefinementRequest
    decision: ProposalDecision
    baseline_experiment_run_id: str
    baseline_artifact_id: str
    reflection_report_hash: str
    proposal_hash: str
    comparison_policy: RefinementComparisonPolicy
    refinement_input_hash: str


class CandidateExperiment(StrictSchema):
    experiment_spec_id: str
    experiment_run_id: str
    test_run_id: str
    approval_id: str


class RefinementRun(StrictSchema):
    refinement_run_id: str = Field(default_factory=lambda: str(uuid4()))
    reflection_run_id: str
    proposal_id: str
    decision_id: str
    baseline_artifact_id: str
    baseline_experiment_run_id: str
    candidate_artifact_id: str
    candidate_test_run_id: str | None = None
    candidate_experiment_run_id: str | None = None
    status: Literal["pending_candidate_review", "testing", "experimenting", "completed", "failed"]
    outcome: Outcome | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    failure_category: str | None = None


class RefinementDecision(StrictSchema):
    decision: Literal[
        "keep_baseline",
        "accept_candidate_for_further_validation",
        "need_more_evidence",
        "reject_candidate",
    ]
    reviewer: NonEmptyText
    comment: NonEmptyText


class RefinementReport(StrictSchema):
    experiment_context: dict = Field(default_factory=dict)
    run: RefinementRun
    plan: RefinementPlan
    definition: CandidateMetricDefinition
    candidate: CandidateArtifact
    candidate_experiment: CandidateExperiment | None = None
    comparison: MetricComparisonResult | None = None
    synthetic_data: bool
    warnings: list[str]
    missing_evidence: list[str]
    final_decision: RefinementDecision | None = None
    requires_human_review: Literal[True] = True

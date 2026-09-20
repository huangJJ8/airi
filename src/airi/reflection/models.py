from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from airi.core.schemas import Identifier, NonEmptyText, StrictSchema
from airi.evaluation.models import EvaluationPolicy, MetricEvaluation
from airi.experiments.models import LabelWindow
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR
from airi.skills.models import ScenarioSkill

Mode = Literal["single_metric", "comparison"]
FindingType = Literal[
    "coverage_gap",
    "weak_separation",
    "strong_separation",
    "risk_direction_uncertain",
    "threshold_tradeoff",
    "high_null_rate",
    "small_sample",
    "single_class",
    "few_effective_bins",
    "high_iv_sensitivity",
    "insufficient_evidence",
    "cross_metric_difference",
]
ProposalType = Literal[
    "window_review",
    "aggregation_review",
    "coverage_investigation",
    "null_handling_review",
    "deduplication_review",
    "source_field_review",
    "business_rule_review",
    "threshold_review",
    "additional_dataset_validation",
    "additional_time_snapshot_validation",
]


class ReflectionSpec(StrictSchema):
    experiment_run_ids: list[str] = Field(min_length=1, max_length=3)
    reflection_policy_version: Literal["1.0.0"] = "1.0.0"
    mode: Mode = "single_metric"

    @model_validator(mode="after")
    def cardinality(self):
        if len(set(self.experiment_run_ids)) != len(self.experiment_run_ids):
            raise ValueError("Duplicate experiment IDs")
        if (self.mode == "single_metric") != (len(self.experiment_run_ids) == 1):
            raise ValueError("single_metric requires one ID; comparison requires two or three")
        return self


class DiagnosticBands(StrictSchema):
    coverage_warning_below: float = Field(default=0.85, ge=0, le=1)
    ks_review_below: float = Field(default=0.1, ge=0, le=1)
    ks_strong_at_least: float = Field(default=0.3, ge=0, le=1)
    small_sample_below: int = Field(default=500, ge=1)
    few_bins_below: int = Field(default=3, ge=1)
    iv_sensitivity_above: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.ks_review_below >= self.ks_strong_at_least:
            raise ValueError("Diagnostic KS bands must not overlap")
        return self


class ReflectionPolicy(StrictSchema):
    version: Literal["1.0.0"] = "1.0.0"
    diagnostic_bands: DiagnosticBands = DiagnosticBands()
    max_proposals: int = Field(default=5, ge=1, le=5)


class ReflectionEvidence(StrictSchema):
    experiment_run_id: str
    experiment_report_hash: str
    artifact_id: str
    artifact_version: str
    artifact_hash: str
    metric_ir_hash: str
    dataset_snapshot_id: str
    label_definition_id: str
    observation_time: datetime
    label_window: LabelWindow
    evaluation_policy: EvaluationPolicy
    evaluation: MetricEvaluation
    join_summary: dict[str, int]
    warnings: list[str]
    synthetic_data: bool
    metric_ir: MetricIR | DerivedMetricIR
    scenario_skill: ScenarioSkill


class DiagnosticFinding(StrictSchema):
    finding_id: str
    finding_type: FindingType
    severity: Literal["info", "warning"]
    experiment_run_ids: list[str]
    evidence_refs: list[str] = Field(min_length=1)
    summary: NonEmptyText
    pattern: Literal["strong_separation_with_coverage_gap"] | None = None


class ComparisonMetric(StrictSchema):
    experiment_run_id: str
    metric_name: str
    coverage: float | None
    ks: float | None
    iv: float | None


class MetricComparison(StrictSchema):
    comparable: Literal[True] = True
    experiment_runs: list[str]
    metrics: list[ComparisonMetric]


class RefinementHypothesis(StrictSchema):
    hypothesis_id: Identifier
    statement: NonEmptyText
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    confidence: Literal["low", "medium", "high"]
    status: Literal["unverified"] = "unverified"


class ProposalParameters(StrictSchema):
    candidate_windows: list[int] = Field(default_factory=list, max_length=3)
    candidate_aggregations: list[Literal["sum", "count", "avg", "count_distinct"]] = Field(
        default_factory=list, max_length=4
    )
    candidate_refs: list[str] = Field(default_factory=list, max_length=6)


class RefinementProposal(StrictSchema):
    proposal_type: ProposalType
    target: Identifier
    action: Literal[
        "evaluate_alternative_window",
        "study_aggregation",
        "investigate_coverage",
        "review_null_handling",
        "review_duplicates",
        "review_source_fields",
        "confirm_business_rules",
        "review_existing_candidates",
        "validate_additional_dataset",
        "validate_additional_snapshot",
    ]
    parameters: ProposalParameters = ProposalParameters()
    reason: NonEmptyText
    validation_question: NonEmptyText
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    priority: Literal["low", "medium", "high"] = "medium"
    requires_human_review: Literal[True] = True


class ReflectionLLMOutput(StrictSchema):
    summary: NonEmptyText
    hypotheses: list[RefinementHypothesis] = Field(max_length=5)
    proposals: list[RefinementProposal] = Field(max_length=5)
    missing_evidence: list[NonEmptyText] = Field(max_length=10)


class ReflectionRun(StrictSchema):
    reflection_run_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: Mode
    status: Literal["running", "completed", "diagnostics_only"]
    reflection_policy_version: str
    prompt_version: str
    model: str
    input_evidence_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class EvidenceReference(StrictSchema):
    ref: str
    experiment_run_id: str
    field: str


class ReflectionContext(StrictSchema):
    evidence: list[ReflectionEvidence]
    diagnostics: list[DiagnosticFinding]
    comparison: MetricComparison | None
    policy: ReflectionPolicy
    references: list[EvidenceReference]
    missing_evidence: list[str]


class ReflectionReport(StrictSchema):
    run: ReflectionRun
    experiment_run_ids: list[str]
    evidence: list[ReflectionEvidence]
    diagnostics: list[DiagnosticFinding]
    comparison: MetricComparison | None
    references: list[EvidenceReference]
    summary: str
    llm_summary: str | None = None
    llm_reflection_status: Literal["completed", "unavailable", "rejected"]
    llm_output_kind: Literal["mock", "provider"]
    hypotheses: list[RefinementHypothesis] = Field(default_factory=list)
    refinement_proposals: list[RefinementProposal] = Field(default_factory=list)
    missing_evidence: list[str]
    provenance: dict
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: Literal[True] = True

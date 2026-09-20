"""Phase 6 governed release semantics.

An Artifact is a research SQL product, a MetricVersion is a governed immutable
identity, a Release is a controlled preparation to expose one version, and an
Activation is a registry pointer switch. Those four must never collapse into one.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from airi.approvals.artifacts import canonical_hash
from airi.core.schemas import Identifier, NonEmptyText, StrictSchema, Version
from airi.metric_ir.models import MetricIR


def new_id():
    return str(uuid4())


# A semantic leaf is usually a scalar, but `dimensions` and `filters` are collections.
VersionFieldValue = str | int | float | bool | list[str] | None


class VersionChangedField(StrictSchema):
    """One semantic leaf that moved between two versions of the same family.

    Deliberately wider than the Phase 4 refinement `ChangedField`: a version diff
    compares whole IR leaves, which may be lists rather than scalars.
    """

    field: str
    before: VersionFieldValue = None
    after: VersionFieldValue = None


MetricVersionStatus = Literal["registered", "release_candidate", "active", "retired"]
VersionChangeKind = Literal["initial", "patch", "minor", "major"]

ReleaseStatus = Literal[
    "draft",
    "pending_staging_validation",
    "staging_validated",
    "pending_release_review",
    "approved",
    "active",
    "failed",
    "rolled_back",
]

ReleaseEligibility = Literal["eligible_for_release_review", "need_more_evidence", "not_eligible"]
TargetEnvironment = Literal["staging", "production"]

ReleaseEventType = Literal[
    "version_registered",
    "release_created",
    "staging_validated",
    "release_approved",
    "release_rejected",
    "activated",
    "rollback_requested",
    "rollback_approved",
    "rollback_rejected",
    "rolled_back",
    # Phase 7 additions. The Phase 6 vocabulary above is preserved unchanged.
    "production_environment_registered",
    "production_preflight_validated",
    "production_shadow_started",
    "production_shadow_validated",
    "production_deployment_approved",
    "production_deployed",
    "monitoring_snapshot_ingested",
    "monitoring_alert_created",
    "rollback_started",
    "production_rolled_back",
    "deployment_reconciled",
    "deployment_reconciliation_required",
    "feedback_recorded",
    # Phase 8 additions. Verification, telemetry trust and auditable recovery.
    "production_environment_verified",
    "runtime_identity_verified",
    "production_deployment_evidence_recorded",
    "telemetry_source_registered",
    "telemetry_source_verified",
    "monitoring_expectation_registered",
    "production_verification_recorded",
    "production_rollback_preflight_validated",
    "reconciliation_planned",
    "reconciliation_approved",
    "reconciliation_executed",
    "reconciliation_verified",
    # Phase 9 additions. Desired-state governance, expectation evaluation and
    # enforcement. The service emits these through the same audit table.
    "desired_runtime_state_established",
    "convergence_already_satisfied",
    "convergence_conflict_diagnosed",
    "expectation_evaluated",
    "telemetry_missing_detected",
    "notification_attempted",
    "verification_gate_blocked",
    "verification_gate_passed",
]

# A release may only advance along these edges. `draft -> active` is not a release flow.
RELEASE_TRANSITIONS = {
    "draft": {"pending_staging_validation"},
    "pending_staging_validation": {"staging_validated", "failed"},
    "staging_validated": {"pending_release_review", "failed"},
    "pending_release_review": {"approved", "failed"},
    "approved": {"active"},
    "active": {"rolled_back"},
    "failed": set(),
    "rolled_back": set(),
}


class MetricDefinition(StrictSchema):
    """Business identity of a metric family; versions hang off this, not the reverse."""

    metric_definition_id: str = Field(default_factory=new_id)
    metric_key: Identifier
    display_name: NonEmptyText
    scenario: Literal["invoice_risk"] = "invoice_risk"
    entity_type: Literal["enterprise"] = "enterprise"
    member_metric_names: list[Identifier] = Field(default_factory=list)
    active_version_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VersionChange(StrictSchema):
    """Deterministic classifier output recorded next to the version it produced."""

    kind: VersionChangeKind
    previous_version: Version | None = None
    changed_fields: list[VersionChangedField] = Field(default_factory=list)


class MetricVersionDiff(StrictSchema):
    metric_key: Identifier
    from_version: Version
    to_version: Version
    changed_fields: list[VersionChangedField]
    unchanged: list[str]
    classification: Literal["patch", "minor", "major"]


class MetricVersion(StrictSchema):
    """Immutable governed identity. Updating semantic fields requires a new version."""

    metric_version_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_key: Identifier
    version: Version
    status: MetricVersionStatus = "registered"
    metric_name: Identifier
    display_name: NonEmptyText | None = None
    metric_ir: MetricIR
    metric_ir_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: str
    artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_refinement_run_id: str
    source_temporal_validation_run_id: str
    source_promotion_review_id: str
    version_change: VersionChange
    provenance: dict = Field(default_factory=dict)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    synthetic_data: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def metric_version_content_hash(version: MetricVersion) -> str:
    # Identity, lifecycle status and timestamps are deliberately excluded.
    content = version.model_dump(
        mode="json",
        include={
            "metric_definition_id",
            "metric_key",
            "version",
            "metric_name",
            "display_name",
            "metric_ir",
            "metric_ir_hash",
            "artifact_id",
            "artifact_hash",
            "source_refinement_run_id",
            "source_temporal_validation_run_id",
            "source_promotion_review_id",
            "version_change",
            "provenance",
            "synthetic_data",
        },
    )
    return canonical_hash(content)


class ReleasePolicy(StrictSchema):
    """Versioned research/release policy constants; not an online strategy standard."""

    version: Literal["1.0.0"] = "1.0.0"
    require_temporal_validation: Literal[True] = True
    require_promotion_review: Literal[True] = True
    require_staging_validation: Literal[True] = True
    require_shadow_validation: Literal[True] = True
    require_human_release_review: Literal[True] = True
    max_entity_loss_fraction: float = Field(default=0.05, ge=0, le=1)
    max_new_null_fraction: float = Field(default=0.05, ge=0, le=1)
    max_distribution_shift: float = Field(default=1.0, ge=0)
    logical_activation_only: Literal[True] = True
    synthetic_review_only: Literal[True] = True
    production_requires_verified_environment: Literal[True] = True


class MetricVersionRequest(StrictSchema):
    """The client supplies a reviewed promotion decision, never content."""

    promotion_review_id: NonEmptyText


class ReleaseRequest(StrictSchema):
    metric_version_id: NonEmptyText
    target_environment: TargetEnvironment


class ReleaseDecision(StrictSchema):
    decision: Literal["approved", "rejected", "need_more_evidence"]
    reviewer: NonEmptyText
    comment: NonEmptyText


class ActivationRequest(StrictSchema):
    """Optimistic concurrency guard; omitted means "no version is currently active"."""

    expected_active_version: Version | None = None


class StagingValidationResult(StrictSchema):
    status: Literal["passed", "passed_with_warnings", "failed", "inconclusive"]
    execution_mode: Literal["mock", "spark_test"]
    target_environment: TargetEnvironment
    artifact_id: str
    artifact_hash: str
    metric_ir_hash: str
    execution_run_id: str | None = None
    failure_category: str | None = None
    row_count: int = Field(ge=0, le=1000)
    truncated: bool
    schema_ok: bool
    coverage: float | None
    null_rate: float | None
    non_null_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class ShadowReference(StrictSchema):
    """What the candidate is compared against: the live version, or the lineage baseline."""

    role: Literal["active_metric_version", "lineage_baseline_artifact", "candidate_metric_version"]
    label: NonEmptyText
    artifact_id: str
    artifact_hash: str
    metric_name: Identifier
    metric_version_id: str | None = None
    version: Version | None = None


class ShadowComparisonResult(StrictSchema):
    status: Literal["passed", "passed_with_warnings", "failed", "inconclusive"]
    baseline: ShadowReference
    candidate: ShadowReference
    dataset_snapshot_id: str
    anchor_time: AwareDatetime
    research_dataset_input: Literal[True] = True
    sample_count: int = Field(ge=0)
    matched_entities: int = Field(ge=0)
    baseline_only: int = Field(ge=0)
    candidate_only: int = Field(ge=0)
    value_equal: int = Field(ge=0)
    value_changed: int = Field(ge=0)
    new_null: int = Field(ge=0)
    resolved_null: int = Field(ge=0)
    difference_rate: float | None
    entity_loss_fraction: float | None
    new_null_fraction: float | None
    distribution_delta: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ReleaseValidationReport(StrictSchema):
    release_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    status: Literal["passed", "passed_with_warnings", "failed", "inconclusive"]
    release_eligibility: ReleaseEligibility
    staging: StagingValidationResult
    shadow: ShadowComparisonResult | None = None
    evidence_checks: dict = Field(default_factory=dict)
    findings: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    synthetic_data: bool
    execution_mode: Literal["mock", "spark_test"]
    release_policy_version: str
    logical_activation_only: Literal[True] = True
    production_deployed: Literal[False] = False
    requires_human_review: Literal[True] = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetricRelease(StrictSchema):
    """Preparation to expose one version. Never a deployment.

    ``logical_activation_only`` / ``production_deployed`` are recorded on the release
    itself so an activated release can never be mistaken for a Spark deployment.
    """

    release_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    target_environment: TargetEnvironment
    status: ReleaseStatus = "draft"
    expected_active_version_id: str | None = None
    release_policy: ReleasePolicy = ReleasePolicy()
    release_provenance: dict = Field(default_factory=dict)
    validation_report_hash: str | None = None
    logical_activation_only: Literal[True] = True
    production_deployed: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    validated_at: datetime | None = None
    activated_at: datetime | None = None


class ReleaseReview(StrictSchema):
    release_review_id: str = Field(default_factory=new_id)
    release_id: str
    metric_version_id: str
    decision: Literal["pending", "approved", "rejected", "need_more_evidence"] = "pending"
    reviewer: str | None = None
    comment: str | None = None
    validation_report_hash: str
    previous_decisions: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    production_deployed: Literal[False] = False


class RollbackRequest(StrictSchema):
    target_version: Version
    reason: NonEmptyText


class RollbackDecision(StrictSchema):
    decision: Literal["approved", "rejected"]
    reviewer: NonEmptyText
    comment: NonEmptyText


class RollbackReview(StrictSchema):
    """Rollback is a reviewed pointer move to an existing version; history is never deleted."""

    rollback_review_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_key: Identifier
    from_version_id: str | None = None
    from_version: Version | None = None
    to_version_id: str
    to_version: Version
    reason: NonEmptyText
    decision: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: str | None = None
    comment: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    production_deployed: Literal[False] = False


class MetricReleaseEvent(StrictSchema):
    """Audit log entry. Not an event bus, not a streaming architecture."""

    event_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_version_id: str | None = None
    release_id: str | None = None
    event_type: ReleaseEventType
    actor: NonEmptyText
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

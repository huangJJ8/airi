"""Phase 7 / Phase 8 production lifecycle semantics.

Phase 6 stopped at the registry boundary: an activated version is a pointer, not
a deployment. Phase 7 keeps that inequality and adds a second, strictly stronger
one:

    Registry Active  !=  Production Active
    Production Deployed  !=  Healthy
    Healthy Today  !=  Stable Tomorrow
    Alert  !=  Rollback
    Feedback  !=  Fact about Root Cause
    Rollback  !=  Delete Version

Phase 8 adds the inequalities that separate a *configured* integration from a
*verified* one:

    Configured  !=  Verified
    Connected  !=  Authenticated
    Authenticated  !=  Authorized
    Deploy returned success  !=  Runtime verified
    Monitoring received  !=  Telemetry trusted
    Mismatch detected  !=  Mismatch fixed
    Recovery executed  !=  Convergence verified

Nothing here executes production SQL. A :class:`ProductionDeployment` is a
governed attempt to hand one immutable :class:`~airi.registry.models.MetricVersion`
to a production runtime through a reviewed, evidenced, reversible procedure.
"""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from airi.approvals.artifacts import canonical_hash
from airi.core.schemas import Identifier, NonEmptyText, StrictSchema, Version
from airi.metric_ir.models import MetricIR
from airi.registry.models import ShadowReference


def new_id():
    return str(uuid4())


# ------------------------------------------------------------------- vocabulary

EnvironmentKind = Literal["test", "production"]
ProductionExecutionMode = Literal["mock", "spark_production"]
RuntimeMode = Literal["disabled", "mock", "spark_production"]

DEFAULT_DEPLOYMENT_POLICY_VERSION = "1.0.0"
DEFAULT_MONITORING_POLICY_VERSION = "1.1.0"
DEFAULT_VERIFICATION_POLICY_VERSION = "1.0.0"
DEFAULT_ENFORCEMENT_POLICY_VERSION = "1.0.0"
DEFAULT_EXPECTATION_POLICY_VERSION = "1.0.0"

DeploymentStatus = Literal[
    "created",
    "preflight_validated",
    "shadow_running",
    "shadow_validated",
    "pending_deployment_review",
    "approved",
    "deployed",
    "monitoring",
    "failed",
    "rolled_back",
    "rollback_reconciliation_required",
    "reconciliation_verification_required",
]

RuntimeState = Literal["unknown", "shadow", "active", "failed", "rolled_back", "mismatch"]

DeploymentEventType = Literal[
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
    # Phase 8: verification, telemetry trust and auditable recovery.
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
    # Phase 9: desired-state governance, expectation evaluation and enforcement.
    "desired_runtime_state_established",
    "convergence_already_satisfied",
    "convergence_conflict_diagnosed",
    "expectation_evaluated",
    "telemetry_missing_detected",
    "notification_attempted",
    "verification_gate_blocked",
    "verification_gate_passed",
]

# Phase 8 vocabulary. Defined up front so the Phase 8 models further down can
# reference them without a late model rebuild.
RuntimeAuthMode = Literal["unknown", "nosasl", "ldap", "kerberos", "gateway"]
ProbeCapability = Literal["verified", "not_verified", "unsupported"]
FingerprintSource = Literal["runtime_probe", "synthetic"]
ShadowReadOnlyBasis = Literal[
    "read_only_identity", "isolated_shadow_output", "synthetic_provider", "not_verified"
]
TelemetryFreshness = Literal["fresh", "stale", "late"]
TelemetryTrust = Literal["trusted_source", "synthetic", "unverified"]
VerificationStatus = Literal["verified", "partially_verified", "not_verified", "failed"]
VerificationSectionName = Literal[
    "environment",
    "identity",
    "adapter",
    "deployment",
    "runtime_identity",
    "shadow",
    "monitoring_source",
    "rollback",
    "reconciliation",
]
# `no_snapshot` is retained: Phase 8 callers still read it, and "nothing arrived" is
# a real observation. Phase 9 adds `due_soon` (inside the grace window) and `unknown`
# (the expectation itself cannot be evaluated), so an operator never has to infer
# urgency from a boolean.
ExpectationStatus = Literal["satisfied", "due_soon", "overdue", "no_snapshot", "unknown"]

# Phase 9: `no_op` is a first-class convergence action, not the absence of one.
# "Nothing needed doing" is a proven governance result and must be sayable.
ConvergenceAction = Literal[
    "no_op", "registry_to_runtime", "runtime_to_registry", "manual_investigation"
]
ReconciliationAction = ConvergenceAction
ReconciliationDecision = Literal[
    "align_runtime_to_registry", "align_registry_to_runtime", "keep_mismatch_for_investigation"
]
ReconciliationFinalStatus = Literal[
    "reconciliation_completed",
    "reconciliation_verification_required",
    "reconciliation_failed",
    "reconciliation_not_executed",
    # Phase 9: the desired state was already satisfied; nothing was executed.
    "already_converged",
]

# ------------------------------------------------------------------ Phase 9


# Desired vs Observed are separate facts. A desired state is *established* by a
# governance decision; an observed state is *reported* by a runtime.
DesiredStateSource = Literal["deployment", "rollback", "reconciliation", "manual_governance"]

# The relationship between the three states. Not a workflow status, not a runtime
# fact - see §48-51: the three must not collapse into one enum.
ConvergenceState = Literal["converged", "mismatch", "unknown"]

# Why a compare-and-set failed. A conflict is not a failure, and an expectation
# that no longer holds is not proof that the target is unmet.
ConflictCategory = Literal[
    "target_already_satisfied",
    "stale_expected_state",
    "unexpected_external_change",
    "runtime_unknown",
]

# The verdict of an execution attempt. `already_converged` is distinct from
# `converged`: nothing ran because nothing needed to run.
ConvergenceVerdict = Literal[
    "already_converged",
    "converged",
    "still_mismatch",
    "not_verified",
    "manual_intervention_required",
]

# Missing and late are different findings: one is a silent pipeline, the other a
# slow one. Collapsing them would blame the metric for a transport delay.
TelemetryFindingType = Literal["telemetry_missing", "telemetry_late"]
TelemetrySourceHealthStatus = Literal["healthy", "degraded", "silent", "unknown"]
NotificationStatus = Literal["delivered", "failed", "disabled"]
VerificationEnforcementMode = Literal["report_only", "enforced"]
GateAction = Literal["deploy", "rollback", "reconciliation"]
GateResult = Literal["allowed", "blocked", "allowed_with_override"]
ClosureStatus = Literal["verified", "not_verified", "not_applicable", "blocked"]
ProductionHealthStatus = Literal["healthy", "degraded", "unknown"]


class ConflictDiagnosis(StrictSchema):
    """A structured explanation of a compare-and-set outcome. Never an exception class.

    ``expected`` is what the caller believed, ``actual`` is what the registry
    reports now, ``desired`` is what the desired state asks for. The category is
    derived from those three, so a caller never has to parse a message to find out
    whether it should retry or escalate.
    """

    expected: str | None = None
    actual: str | None = None
    desired: str | None = None
    category: ConflictCategory
    safe_to_retry: bool
    requires_manual_review: bool
    detail: NonEmptyText


# `created -> deployed` is not a deployment flow. Phase 8 adds exactly one edge:
# an unreconciled partial rollback may be re-verified back to `rolled_back`, or
# be named as still-unreconciled.
DEPLOYMENT_TRANSITIONS: dict[str, set[str]] = {
    "created": {"preflight_validated", "failed"},
    "preflight_validated": {"shadow_running", "failed"},
    "shadow_running": {"shadow_validated", "failed"},
    "shadow_validated": {"pending_deployment_review", "failed"},
    "pending_deployment_review": {"approved", "failed"},
    "approved": {"deployed", "failed"},
    "deployed": {"monitoring", "rolled_back", "failed"},
    "monitoring": {"rolled_back", "failed", "rollback_reconciliation_required"},
    "failed": set(),
    "rolled_back": set(),
    "rollback_reconciliation_required": {
        "rolled_back",
        "failed",
        "reconciliation_verification_required",
    },
    "reconciliation_verification_required": {"rolled_back", "failed"},
}


# ------------------------------------------------------- production environment


class ProductionEnvironmentRequest(StrictSchema):
    """What an operator may declare. `verified` is never client-supplied."""

    environment_id: Identifier
    name: NonEmptyText
    environment_kind: EnvironmentKind
    execution_mode: ProductionExecutionMode
    deployment_enabled: bool = False
    environment_validation_run_id: str | None = None
    synthetic_profile: bool = False


class ProductionEnvironmentProfile(StrictSchema):
    """A separately accepted production environment. `spark_test` is never reused."""

    environment_id: Identifier
    name: NonEmptyText
    engine: Literal["spark_sql"] = "spark_sql"
    environment_kind: EnvironmentKind
    execution_mode: ProductionExecutionMode
    read_only_preflight: Literal[True] = True
    deployment_enabled: bool = False
    environment_validation_run_id: str | None = None
    verification_source: Literal["environment_validation_report", "synthetic_profile", "none"] = (
        "none"
    )
    verified: bool = False
    synthetic_profile: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def deployable(self) -> bool:
        return (
            self.environment_kind == "production"
            and self.verified
            and self.deployment_enabled
            and self.read_only_preflight
        )


# ---------------------------------------------------------------- deployment IO


class ProductionDeploymentRequest(StrictSchema):
    """What a client may submit to start a production flow.

    Only the two governance handles: a release that is already approved and a
    production environment that is already verified. SQL, IR, version and
    artifact content are resolved from the registry lineage, never from here.
    """

    release_id: str
    environment_id: Identifier


class DeploymentPackage(StrictSchema):
    """Deterministic, immutable handover object.

    Assembled from the registry lineage. A client may name a release and an
    environment; it may never contribute IR, SQL, version or artifact content.
    """

    deployment_package_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    metric_version_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    metric_ir: MetricIR
    metric_ir_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_id: str
    artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sql: str = Field(min_length=1, max_length=20000)
    sql_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    release_id: str
    release_review_id: str
    release_validation_report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_id: Identifier
    environment_profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    synthetic_data: bool
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# What the package hash covers: metric version identity, artifact, SQL, release
# evidence and target environment. Identity and timestamps are excluded.
_PACKAGE_HASH_FIELDS = (
    "metric_definition_id",
    "metric_version_id",
    "metric_version_content_hash",
    "metric_key",
    "version",
    "metric_ir_hash",
    "artifact_id",
    "artifact_hash",
    "sql_hash",
    "release_id",
    "release_review_id",
    "release_validation_report_hash",
    "environment_id",
    "environment_profile_hash",
    "environment_fingerprint_hash",
)


def deployment_package_hash(package: DeploymentPackage) -> str:
    return canonical_hash(package.model_dump(mode="json", include=set(_PACKAGE_HASH_FIELDS)))


class ProductionDeploymentPlan(StrictSchema):
    """First version offers `shadow` and `manual_cutover` only.

    No canary percentage rollout and no blue-green orchestration until a real
    environment supports them.
    """

    deployment_plan_id: str = Field(default_factory=new_id)
    package_id: str
    target_environment: Identifier
    expected_active_version: Version | None = None
    deployment_strategy: Literal["shadow", "manual_cutover"] = "shadow"
    cutover_mode: Literal["manual"] = "manual"
    preflight_checks: list[str] = Field(default_factory=list)
    synthetic_data: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PreflightCheck(StrictSchema):
    name: str = Field(min_length=1, max_length=64)
    status: Literal["passed", "failed", "not_verified"]
    detail: str = Field(max_length=500)


class ProductionPreflightResult(StrictSchema):
    deployment_id: str
    status: Literal["passed", "failed"]
    checks: list[PreflightCheck] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    read_only_preflight: Literal[True] = True
    production_deployed: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionDeploymentPolicy(StrictSchema):
    """Versioned production gate constants. Not an online strategy standard."""

    version: Literal["1.0.0"] = "1.0.0"
    require_environment_verification: Literal[True] = True
    require_release_approval: Literal[True] = True
    require_preflight: Literal[True] = True
    require_production_shadow: Literal[True] = True
    require_human_deployment_review: Literal[True] = True
    require_authenticated_deployer: Literal[True] = True
    shadow_write_business_output: Literal[False] = False
    automatic_rollback: Literal[False] = False
    cutover_mode: Literal["manual"] = "manual"


class ProductionValidationResult(StrictSchema):
    status: Literal["passed", "failed", "not_verified"]
    checks: list[PreflightCheck] = Field(default_factory=list)
    provider_query_id: str | None = None
    read_only: Literal[True] = True
    detail: str | None = Field(default=None, max_length=500)
    # Phase 8: what the live runtime actually reported during the preflight probe.
    # `None` means the adapter could not observe it - never an invented value.
    authoritative: bool = False
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    auth_mode: "RuntimeAuthMode" = "unknown"


# -------------------------------------------------------------------- monitoring


class MonitoringDistributionBin(StrictSchema):
    lower: float | None = None
    upper: float | None = None
    count: int = Field(ge=0)
    is_null: bool = False


class MonitoringDistribution(StrictSchema):
    """Aggregate bins only. Entity rows, labels and raw invoices have no field here."""

    bins: list[MonitoringDistributionBin] = Field(default_factory=list, max_length=64)
    total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def aligned(self) -> Self:
        nulls = [index for index, item in enumerate(self.bins) if item.is_null]
        if len(nulls) > 1 or (nulls and nulls[0] != len(self.bins) - 1):
            raise ValueError("At most one NULL bin, and it must be last")
        if self.bins and sum(item.count for item in self.bins) != self.total:
            raise ValueError("Bin counts must sum to total")
        if self.bins and self.total == 0:
            raise ValueError("A non-empty distribution needs a positive total")
        return self


MonitoringFindingType = Literal[
    "execution_failure",
    "latency_degradation",
    "coverage_drop",
    "null_rate_increase",
    "duplicate_increase",
    "population_shift",
    "missing_monitoring_data",
]

AlertType = MonitoringFindingType | Literal["deployment_state_mismatch"]
AlertSeverity = Literal["info", "warning", "critical"]
AlertStatus = Literal["open", "acknowledged", "resolved", "dismissed"]
RuntimeFindingSeverity = Literal["warning", "critical"]


class MonitoringPolicy(StrictSchema):
    """Versioned alert thresholds. Thresholds raise alerts, they never roll back.

    Phase 8 adds telemetry-freshness bounds and a provenance string. A policy is
    versioned *and* hashable so a historical snapshot or alert can name the exact
    thresholds it was judged under; a later policy edit never recomputes it.
    """

    version: Literal["1.0.0", "1.1.0"] = DEFAULT_MONITORING_POLICY_VERSION
    provenance: NonEmptyText = "airi-default-monitoring-policy"
    window_hours: int = Field(default=24, ge=1, le=168)
    max_execution_failure_rate: float = Field(default=0.2, ge=0, le=1)
    latency_review_above_ms: int = Field(default=600_000, ge=0)
    coverage_drop_review: float = Field(default=0.05, ge=0, le=1)
    null_rate_increase_review: float = Field(default=0.05, ge=0, le=1)
    duplicate_rate_review: float = Field(default=0.02, ge=0, le=1)
    psi_review_above: float = Field(default=0.2, ge=0)
    psi_critical_above: float = Field(default=0.35, ge=0)
    missing_snapshot_hours: int = Field(default=48, ge=1, le=720)
    psi_epsilon: float = Field(default=0.0001, gt=0, le=0.01)
    # Phase 8: observation-to-receipt lag bounds. Freshness never rewrites history.
    telemetry_fresh_hours: int = Field(default=1, ge=0, le=168)
    telemetry_stale_hours: int = Field(default=24, ge=1, le=720)

    @model_validator(mode="after")
    def ordered_freshness_bounds(self) -> Self:
        if self.telemetry_stale_hours < self.telemetry_fresh_hours:
            raise ValueError("telemetry_stale_hours must be >= telemetry_fresh_hours")
        return self

    @property
    def policy_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class MonitoringExecution(StrictSchema):
    status: Literal["success", "failed", "timeout"]
    duration_ms: int = Field(ge=0)
    row_count: int = Field(ge=0)


class MonitoringQuality(StrictSchema):
    coverage: float | None = Field(default=None, ge=0, le=1)
    null_rate: float | None = Field(default=None, ge=0, le=1)
    duplicate_rate: float | None = Field(default=None, ge=0, le=1)


class MonitoringSnapshotRequest(StrictSchema):
    """Push summary from an external execution system. Aggregate metrics only.

    Phase 8 requires the snapshot to name the registered telemetry source it came
    from. An unattributed runtime summary is not telemetry.
    """

    deployment_id: str
    telemetry_source_id: Identifier
    observation_time: AwareDatetime
    execution: MonitoringExecution
    quality: MonitoringQuality
    distribution: MonitoringDistribution = MonitoringDistribution()
    source_system: NonEmptyText
    source_event_id: NonEmptyText

    @field_validator("observation_time", mode="before")
    @classmethod
    def iso_datetime(cls, value):
        # HTTP bodies carry ISO strings; strict mode then validates the datetime.
        if isinstance(value, str):
            if "T" not in value:
                raise ValueError("Explicit ISO datetime required")
            return datetime.fromisoformat(value)
        return value


class MonitoringSnapshot(StrictSchema):
    monitoring_snapshot_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    observation_time: AwareDatetime
    execution: MonitoringExecution
    quality: MonitoringQuality
    distribution: MonitoringDistribution
    source_system: NonEmptyText
    source_event_id: NonEmptyText
    publisher_actor_id: str
    # Phase 8 telemetry provenance. `received_at` is the server clock; the
    # observation time is the producer's. They are never conflated.
    telemetry_source_id: str | None = None
    telemetry_trust: "TelemetryTrust" = "unverified"
    telemetry_freshness: "TelemetryFreshness" = "late"
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    observation_lag_hours: float | None = None
    monitoring_policy_version: str = Field(default=DEFAULT_MONITORING_POLICY_VERSION, max_length=16)
    monitoring_policy_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    baseline_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    psi: float | None = None
    psi_status: Literal["available", "not_available", "baseline_mismatch"] = "not_available"
    findings: list[dict] = Field(default_factory=list)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MonitoringBaseline(StrictSchema):
    """Per-version reference. Prefers the production shadow, else release validation."""

    deployment_id: str
    metric_version_id: str
    source: Literal["production_shadow", "first_accepted_production_window", "release_validation"]
    distribution: MonitoringDistribution
    row_count: int = Field(ge=0)
    coverage: float | None = None
    null_rate: float | None = None
    duplicate_rate: float | None = None
    latency_ms: int | None = None
    synthetic_data: bool
    observation_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def baseline_hash(self) -> str:
        """Stable identity of the comparison basis that PSI was computed against."""
        return canonical_hash(
            self.model_dump(
                mode="json",
                include={
                    "deployment_id",
                    "metric_version_id",
                    "source",
                    "distribution",
                    "row_count",
                    "coverage",
                    "null_rate",
                    "duplicate_rate",
                    "latency_ms",
                    "observation_time",
                },
            )
        )


class MonitoringFinding(StrictSchema):
    type: MonitoringFindingType
    severity: RuntimeFindingSeverity
    evidence: dict = Field(default_factory=dict)
    dedup_key: str = Field(pattern=r"^[a-f0-9]{64}$")


class MetricAlert(StrictSchema):
    """Deterministic alert. Severity comes from policy, action never from the model."""

    alert_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    type: AlertType
    severity: AlertSeverity
    status: AlertStatus = "open"
    evidence: dict = Field(default_factory=dict)
    dedup_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurrences: int = Field(default=1, ge=1)
    recommended_action: Literal["observe", "investigate", "consider_rollback"] = "observe"
    monitoring_policy_version: str = Field(default=DEFAULT_MONITORING_POLICY_VERSION, max_length=16)
    decision: str | None = None
    reviewer: str | None = None
    comment: str | None = None
    automatic_action_taken: Literal[False] = False
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AlertDecision(StrictSchema):
    decision: Literal["acknowledge", "resolve", "dismiss"]
    comment: NonEmptyText


class MonitoringIngestResult(StrictSchema):
    """One ingested snapshot plus the alerts it opened or refreshed."""

    snapshot: MonitoringSnapshot
    alerts: list[MetricAlert] = Field(default_factory=list)
    created_alert_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# -------------------------------------------------------------------- runtime


class ProductionShadowObservation(StrictSchema):
    """Raw provider observation. Bins only; never entity rows."""

    execution_success: bool
    provider_query_id: str | None = None
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    runtime_identity: str | None = None
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    columns: list[str] = Field(default_factory=list, max_length=64)
    row_count: int = Field(default=0, ge=0)
    entity_count: int = Field(default=0, ge=0)
    non_null_count: int = Field(default=0, ge=0)
    null_count: int = Field(default=0, ge=0)
    duplicate_entities: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    bins: list[MonitoringDistributionBin] = Field(default_factory=list, max_length=64)
    failure_category: str | None = None
    read_only_basis: "ShadowReadOnlyBasis" = "synthetic_provider"
    detail: str | None = Field(default=None, max_length=500)
    write_business_output: Literal[False] = False
    synthetic_data: bool
    observation_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionShadowResult(StrictSchema):
    """Governed shadow verdict. A changed value is evidence, not an error."""

    status: Literal["passed", "passed_with_warnings", "failed", "inconclusive"]
    deployment_id: str
    metric_version_id: str
    metric_key: Identifier
    environment_id: Identifier
    runtime_mode: RuntimeMode
    provider_query_id: str | None = None
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    runtime_identity: str | None = None
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    columns: list[str] = Field(default_factory=list)
    read_only_basis: "ShadowReadOnlyBasis" = "synthetic_provider"
    execution_success: bool
    row_count: int = Field(ge=0)
    entity_count: int = Field(ge=0)
    coverage: float | None
    null_rate: float | None
    duplicate_rate: float | None
    latency_ms: int = Field(ge=0)
    distribution: MonitoringDistribution = MonitoringDistribution()
    baseline: ShadowReference | None = None
    write_business_output: Literal[False] = False
    production_deployed: Literal[False] = False
    findings: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    synthetic_data: bool
    observation_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionDeploymentResult(StrictSchema):
    status: Literal["deployed", "failed", "not_verified"]
    provider_job_id: str | None = None
    provider_query_id: str | None = None
    provider_application_id: str | None = None
    schedule_id: str | None = None
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_category: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionRollbackResult(StrictSchema):
    status: Literal["rolled_back", "failed", "not_verified"]
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    provider_query_id: str | None = None
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_category: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionDeploymentStatus(StrictSchema):
    deployment_id: str
    runtime_state: RuntimeState
    active_version_id: str | None = None
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    detail: str | None = Field(default=None, max_length=500)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionRuntimeBinding(StrictSchema):
    """Runtime identity. Missing provider identifiers stay null, never invented."""

    deployment_id: str
    metric_version_id: str
    environment_id: Identifier
    provider_job_id: str | None = None
    provider_query_id: str | None = None
    provider_application_id: str | None = None
    schedule_id: str | None = None
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    activated_at: datetime | None = None


class ProductionDeploymentReview(StrictSchema):
    deployment_review_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_version_id: str
    decision: Literal["pending", "approved", "rejected", "need_more_evidence"] = "pending"
    reviewer: str | None = None
    reviewer_actor_id: str | None = None
    reviewer_roles: list[str] = Field(default_factory=list)
    # Phase 8 identity evidence: who decided, under which trusted source and
    # issuer. Recorded at decision time so a later role change stays auditable.
    reviewer_auth_source: str | None = None
    reviewer_issuer: str | None = None
    comment: str | None = None
    package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_decisions: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    production_deployed: Literal[False] = False


class DeploymentReviewDecision(StrictSchema):
    decision: Literal["approved", "rejected", "need_more_evidence"]
    comment: NonEmptyText


class ProductionRollbackRequest(StrictSchema):
    target_version: Version
    reason: NonEmptyText
    alert_ids: list[str] = Field(default_factory=list, max_length=50)


class ProductionRollbackDecision(StrictSchema):
    decision: Literal["approved", "rejected"]
    comment: NonEmptyText


class ProductionRollbackReview(StrictSchema):
    """Two-phase rollback: provider first, registry second. Partial states are named."""

    rollback_review_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_key: Identifier
    from_version_id: str
    from_version: Version
    to_version_id: str
    to_version: Version
    reason: NonEmptyText
    alert_ids: list[str] = Field(default_factory=list)
    preflight: "ProductionRollbackPreflight | None" = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: str | None = None
    reviewer_actor_id: str | None = None
    reviewer_roles: list[str] = Field(default_factory=list)
    reviewer_auth_source: str | None = None
    reviewer_issuer: str | None = None
    comment: str | None = None
    production_rollback_status: Literal["not_started", "started", "succeeded", "failed"] = (
        "not_started"
    )
    registry_reconciliation_status: Literal[
        "pending", "reconciled", "required", "skipped", "already_satisfied"
    ] = "pending"
    runtime_verified: bool = False
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    runtime_identity: str | None = None
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    automatic: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    # ------------------------------------------- Phase 9: already-satisfied fix
    # A rollback whose registry target is already met must not report a conflict.
    no_action_required: bool = False
    conflict_diagnosis: ConflictDiagnosis | None = None


# ------------------------------------------------------------------- feedback


class FeedbackEvidenceRef(StrictSchema):
    kind: Literal["alert", "monitoring_snapshot", "deployment", "release", "metric_version"]
    ref_id: str = Field(min_length=1, max_length=64)


class FeedbackEvent(StrictSchema):
    """A recorded production fact. Deliberately carries no interpretation.

    Reflection explains; feedback only reports. Merging the two would let a
    runtime signal become a research conclusion without a human in between.
    """

    feedback_event_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_version_id: str | None = None
    deployment_id: str | None = None
    source: Literal["monitoring_alert", "runtime_monitoring", "manual", "external_system"]
    type: Literal[
        "runtime_failure",
        "data_quality_degradation",
        "distribution_drift",
        "performance_degradation",
        "manual_business_feedback",
    ]
    severity: AlertSeverity
    evidence_refs: list[FeedbackEvidenceRef] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)
    recorded_by: str
    record_kind: Literal["fact"] = "fact"
    interpretation: Literal[None] = None
    automatic_research_started: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackRequest(StrictSchema):
    deployment_id: str
    type: Literal[
        "runtime_failure",
        "data_quality_degradation",
        "distribution_drift",
        "performance_degradation",
        "manual_business_feedback",
    ]
    severity: AlertSeverity
    evidence_refs: list[FeedbackEvidenceRef] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)


class FeedbackResearchRequest(StrictSchema):
    """Human-authored request to revalidate or open a new experiment. Never automatic."""

    research_request_id: str = Field(default_factory=new_id)
    feedback_event_id: str
    metric_definition_id: str
    metric_version_id: str | None = None
    request_type: Literal["revalidation", "new_experiment"]
    rationale: NonEmptyText
    decision: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: str | None = None
    reviewer_actor_id: str | None = None
    comment: str | None = None
    automatic_reflection: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None


class FeedbackResearchDecision(StrictSchema):
    decision: Literal["approved", "rejected"]
    comment: NonEmptyText


class ResearchRequestCreate(StrictSchema):
    request_type: Literal["revalidation", "new_experiment"]
    rationale: NonEmptyText


# --------------------------------------------------------------- reconciliation


class DeploymentReconciliation(StrictSchema):
    """Detect only. Choosing a winner between registry and runtime is a human call."""

    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    registry_active_version_id: str | None = None
    registry_active_version: Version | None = None
    production_runtime_state: RuntimeState
    production_active_version_id: str | None = None
    production_active_version: Version | None = None
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    runtime_identity: str | None = None
    provider_application_id: str | None = None
    status: Literal["consistent", "mismatch", "unknown"]
    automatic_correction: Literal[False] = False
    findings: list[dict] = Field(default_factory=list)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ------------------------------------------------------------------ deployment


class ProductionDeployment(StrictSchema):
    """One governed production handover, from package to live runtime."""

    deployment_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    release_id: str
    environment_id: Identifier
    status: DeploymentStatus = "created"
    package: DeploymentPackage
    plan: ProductionDeploymentPlan
    deployment_policy: ProductionDeploymentPolicy = ProductionDeploymentPolicy()
    monitoring_policy: MonitoringPolicy = MonitoringPolicy()
    preflight: ProductionPreflightResult | None = None
    shadow: ProductionShadowResult | None = None
    review_id: str | None = None
    rollback_review_id: str | None = None
    runtime_mode: RuntimeMode = "disabled"
    runtime_binding: ProductionRuntimeBinding | None = None
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    production_deployed: bool = False
    real_environment_verified: bool = False
    deployment_evidence_id: str | None = None
    verification_run_id: str | None = None
    synthetic_data: bool
    requested_by: str | None = None
    monitoring_baseline: MonitoringBaseline | None = None
    reconciliation: DeploymentReconciliation | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deployed_at: datetime | None = None
    rolled_back_at: datetime | None = None

    @model_validator(mode="after")
    def honest_deployment_flag(self) -> Self:
        # Only a real, verified production runtime may claim a production deployment.
        if self.production_deployed and (
            self.runtime_mode != "spark_production" or not self.runtime_identity_verified
        ):
            raise ValueError("production_deployed requires a verified production runtime")
        if self.production_deployed and not self.environment_fingerprint_hash:
            raise ValueError("production_deployed requires a verified environment fingerprint")
        return self


def monitoring_snapshot_content_hash(snapshot: MonitoringSnapshot) -> str:
    return canonical_hash(
        snapshot.model_dump(
            mode="json",
            include={
                "deployment_id",
                "metric_definition_id",
                "metric_version_id",
                "metric_key",
                "version",
                "observation_time",
                "execution",
                "quality",
                "distribution",
                "source_system",
                "source_event_id",
                "publisher_actor_id",
            },
        )
    )


def metric_alert_hash(alert: MetricAlert) -> str:
    return canonical_hash(alert.model_dump(mode="json"))


def feedback_event_hash(event: FeedbackEvent) -> str:
    return canonical_hash(event.model_dump(mode="json"))


# ============================================================ Phase 8: real
# production verification, trusted telemetry and auditable recovery.
#
# Nothing in this section changes a Phase 7 object's meaning. It adds the
# evidence that separates "configured" from "verified".

# ------------------------------------------------- environment fingerprint


class ProductionEnvironmentFingerprint(StrictSchema):
    """What the runtime *is*, observed rather than configured.

    Contains no credential material: engine/identity/cluster facts only. The
    hash is the anti-swap anchor - a package built against cluster A must never
    be executed on cluster B.
    """

    environment_fingerprint_id: str = Field(default_factory=new_id)
    environment_id: Identifier
    engine: Literal["spark_sql"] = "spark_sql"
    engine_version: str | None = None
    cluster_identifier: str | None = None
    database_identity: str | None = None
    catalog_identity: str | None = None
    session_timezone: str | None = None
    auth_mode: RuntimeAuthMode = "unknown"
    runtime_user: str | None = None
    read_only_attested: bool = False
    query_id_available: bool | None = None
    cancel_capability: ProbeCapability = "not_verified"
    reachable: bool = False
    runtime_identity_verified: bool = False
    source: FingerprintSource = "runtime_probe"
    fingerprint_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_by: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def verified(self) -> bool:
        """A fingerprint is only usable when the runtime answered for itself."""
        return self.reachable and self.source == "runtime_probe"


_FINGERPRINT_HASH_FIELDS = (
    "environment_id",
    "engine",
    "engine_version",
    "cluster_identifier",
    "database_identity",
    "catalog_identity",
    "session_timezone",
    "auth_mode",
    "runtime_user",
    "read_only_attested",
)


def environment_fingerprint_hash(fingerprint: ProductionEnvironmentFingerprint) -> str:
    return canonical_hash(
        fingerprint.model_dump(mode="json", include=set(_FINGERPRINT_HASH_FIELDS))
    )


def build_fingerprint(**fields) -> ProductionEnvironmentFingerprint:
    """Hash a fingerprint from its identity fields, never from a caller value."""
    draft = ProductionEnvironmentFingerprint(fingerprint_hash="0" * 64, **fields)
    return draft.model_copy(update={"fingerprint_hash": environment_fingerprint_hash(draft)})


class ProductionRuntimeProbe(StrictSchema):
    """Structured evidence from a live connectivity/identity probe.

    Every field a provider cannot answer stays ``None``. There is no default that
    could be mistaken for a successful verification.
    """

    probe_id: str = Field(default_factory=new_id)
    environment_id: Identifier
    adapter: str
    reachable: bool = False
    authenticated: bool = False
    engine_version: str | None = None
    session_timezone: str | None = None
    current_database: str | None = None
    catalog_identity: str | None = None
    cluster_identifier: str | None = None
    runtime_identity: str | None = None
    runtime_identity_source: Literal["none", "session", "provider_attested"] = "none"
    auth_mode: RuntimeAuthMode = "unknown"
    read_only_capability: ProbeCapability = "not_verified"
    query_id_available: bool | None = None
    cancel_capability: ProbeCapability = "not_verified"
    provider_application_id: str | None = None
    provider_query_id: str | None = None
    capabilities: dict[str, ProbeCapability] = Field(default_factory=dict)
    failure_category: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    synthetic: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def runtime_identity_verified(self) -> bool:
        return self.runtime_identity is not None and self.runtime_identity_source != "none"

    def fingerprint(
        self, *, environment_id: str, read_only_attested: bool, observed_by: str | None = None
    ) -> ProductionEnvironmentFingerprint:
        return build_fingerprint(
            environment_id=environment_id,
            engine_version=self.engine_version,
            cluster_identifier=self.cluster_identifier,
            database_identity=self.current_database,
            catalog_identity=self.catalog_identity,
            session_timezone=self.session_timezone,
            auth_mode=self.auth_mode,
            runtime_user=self.runtime_identity,
            read_only_attested=read_only_attested and self.read_only_capability == "verified",
            query_id_available=self.query_id_available,
            cancel_capability=self.cancel_capability,
            reachable=self.reachable,
            runtime_identity_verified=self.runtime_identity_verified,
            source="synthetic" if self.synthetic else "runtime_probe",
            observed_by=observed_by,
            observed_at=self.observed_at,
        )


# ------------------------------------------------------- deployment evidence


class ProductionDeploymentEvidence(StrictSchema):
    """What the runtime confirmed after a deploy, not what we asked for.

    ``status_confirmed`` is the double-confirmation flag of §19: a provider that
    answers "deployed" to ``deploy`` but reports a different runtime state to
    ``status`` has not deployed anything we may claim.
    """

    deployment_evidence_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_version_id: str
    metric_key: Identifier
    version: Version
    environment_id: Identifier
    package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    adapter: str
    authoritative: bool = False
    deploy_status: Literal["deployed", "failed", "not_verified"]
    runtime_identity: str | None = None
    runtime_identity_verified: bool = False
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    provider_query_id: str | None = None
    status_confirmed: bool = False
    status_runtime_state: RuntimeState | None = None
    status_active_version_id: str | None = None
    production_deployed: bool = False
    detail: str | None = Field(default=None, max_length=500)
    synthetic: bool = False
    deployed_at: datetime | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def deployment_evidence_hash(evidence: ProductionDeploymentEvidence) -> str:
    return canonical_hash(
        evidence.model_dump(
            mode="json",
            include={
                "deployment_id",
                "metric_version_id",
                "metric_key",
                "version",
                "environment_id",
                "package_hash",
                "environment_fingerprint_hash",
                "adapter",
                "authoritative",
                "deploy_status",
                "runtime_identity",
                "runtime_identity_verified",
                "provider_job_id",
                "provider_application_id",
                "provider_query_id",
                "status_confirmed",
                "status_runtime_state",
                "status_active_version_id",
                "production_deployed",
                "synthetic",
                "deployed_at",
            },
        )
    )


# --------------------------------------------------------- trusted telemetry


class TrustedTelemetrySourceRequest(StrictSchema):
    """What an operator declares. `verified` is decided server-side."""

    telemetry_source_id: Identifier
    source_system: NonEmptyText
    environment_id: Identifier
    auth_identity: NonEmptyText
    schema_version: Version = "1.0.0"


class TrustedTelemetrySource(StrictSchema):
    """A registered producer of runtime telemetry for exactly one environment."""

    telemetry_source_id: Identifier
    source_system: NonEmptyText
    environment_id: Identifier
    auth_identity: NonEmptyText
    schema_version: Version = "1.0.0"
    verified: bool = False
    verification_note: str | None = Field(default=None, max_length=500)
    registered_by: str
    synthetic: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TrustedTelemetrySourceVerification(StrictSchema):
    """An admin's attestation that a producer authenticates as declared.

    AIRI cannot verify a remote producer's credential from inside its own
    process, so this records *who vouched for it and why* instead of pretending
    the check was automatic.
    """

    note: NonEmptyText
    synthetic: bool = False


class MonitoringExpectationRequest(StrictSchema):
    """Declares when telemetry *should* arrive. It schedules nothing."""

    deployment_id: str
    expected_interval_hours: int = Field(default=24, ge=1, le=720)
    grace_hours: int = Field(default=6, ge=0, le=168)
    telemetry_source_id: Identifier | None = None


class MonitoringExpectation(StrictSchema):
    monitoring_expectation_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    environment_id: Identifier
    telemetry_source_id: str | None = None
    expected_interval_hours: int = Field(default=24, ge=1, le=720)
    grace_hours: int = Field(default=6, ge=0, le=168)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MonitoringExpectationStatus(StrictSchema):
    """Result of evaluating an expectation. Evaluation alone never alerts."""

    deployment_id: str
    monitoring_expectation_id: str | None = None
    status: ExpectationStatus
    expected_interval_hours: int | None = None
    grace_hours: int | None = None
    last_observation_time: datetime | None = None
    last_freshness: TelemetryFreshness | None = None
    hours_since_last_observation: float | None = None
    due_at: datetime | None = None
    snapshot_count: int = Field(default=0, ge=0)
    detail: str = Field(max_length=500)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # ---------------------------------------- Phase 9: operational evaluation
    telemetry_source_id: str | None = None
    # `telemetry_missing` and `telemetry_late` are separate findings. A silent
    # pipeline and a slow one are not the same operational problem.
    findings: list[TelemetryFindingType] = Field(default_factory=list)
    # Identifies the missed window, so repeated evaluations of the same window do
    # not each open an alert while a new window still can.
    window_key: str | None = Field(default=None, max_length=64)
    policy_version: str = Field(default=DEFAULT_EXPECTATION_POLICY_VERSION, max_length=16)


# ------------------------------------------------------------- verification


class ProductionVerificationPolicy(StrictSchema):
    """Versioned production admission gate. Never client-overridable."""

    version: Literal["1.0.0"] = DEFAULT_VERIFICATION_POLICY_VERSION
    require_environment_verified: Literal[True] = True
    require_identity_verified: Literal[True] = True
    require_authoritative_adapter: Literal[True] = True
    require_runtime_identity: Literal[True] = True
    require_shadow_verified: Literal[True] = True
    require_telemetry_verified: Literal[True] = True
    # Not every organisation permits manufacturing a real production rollback,
    # so rollback may stay unverified - but the report must say so explicitly.
    require_rollback_verified: Literal[False] = False


VERIFICATION_REQUIRED_SECTIONS: tuple[str, ...] = (
    "environment",
    "identity",
    "adapter",
    "deployment",
    "runtime_identity",
    "shadow",
    "monitoring_source",
)


class VerificationSection(StrictSchema):
    name: VerificationSectionName
    status: VerificationStatus
    detail: str = Field(max_length=500)
    evidence: dict = Field(default_factory=dict)


class ProductionVerificationReport(StrictSchema):
    """Per-section verdicts. One aggregate boolean would hide a partial pass."""

    production_verification_run_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    environment_id: Identifier
    policy: ProductionVerificationPolicy = ProductionVerificationPolicy()
    sections: list[VerificationSection] = Field(default_factory=list)
    overall: VerificationStatus
    production_runtime_verified: bool = False
    production_deployed: bool = False
    not_verified: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def verification_report_hash(report: ProductionVerificationReport) -> str:
    return canonical_hash(
        report.model_dump(
            mode="json",
            include={
                "deployment_id",
                "metric_definition_id",
                "metric_version_id",
                "environment_id",
                "policy",
                "sections",
                "overall",
                "production_runtime_verified",
                "production_deployed",
                "not_verified",
                "blocking",
            },
        )
    )


# ---------------------------------------------- rollback preflight (Phase 8)


class ProductionRollbackPreflight(StrictSchema):
    deployment_id: str
    target_version_id: str
    target_version: Version
    status: Literal["passed", "failed"]
    checks: list[PreflightCheck] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ------------------------------------------- reconciliation plan/review/result


class ReconciliationPlan(StrictSchema):
    """A proposed recovery. Never an automatic winner. §39: no side may self-declare truth.

    Phase 9 evolves this rather than forking it (§59): the same object now carries
    the three-way comparison and, when the desired state is already satisfied, a
    ``no_op`` plan that is a *result* rather than an empty plan.
    """

    reconciliation_plan_id: str = Field(default_factory=new_id)
    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    verdict: ConvergenceState = "unknown"
    registry_active_version_id: str | None = None
    registry_active_version: Version | None = None
    production_runtime_state: RuntimeState
    production_active_version_id: str | None = None
    production_active_version: Version | None = None
    environment_id: Identifier
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    runtime_identity: str | None = None
    provider_application_id: str | None = None
    deployment_evidence_id: str | None = None
    latest_deployment_review_id: str | None = None
    rollback_review_ids: list[str] = Field(default_factory=list)
    alert_ids: list[str] = Field(default_factory=list)
    possible_actions: list[ReconciliationAction] = Field(default_factory=list)
    recommended_action: ReconciliationAction = "manual_investigation"
    rationale: NonEmptyText
    automatic_correction: Literal[False] = False
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # ------------------------------------------------ Phase 9: three-way model
    desired_state_id: str | None = None
    desired_metric_version_id: str | None = None
    desired_version: Version | None = None
    desired_source: DesiredStateSource | None = None
    convergence_state: ConvergenceState = "unknown"
    target_already_satisfied: bool = False
    conflict: ConflictDiagnosis | None = None


class ReconciliationReviewCreate(StrictSchema):
    comment: NonEmptyText


class ReconciliationReviewDecision(StrictSchema):
    decision: ReconciliationDecision
    comment: NonEmptyText


class ReconciliationReview(StrictSchema):
    reconciliation_review_id: str = Field(default_factory=new_id)
    reconciliation_plan_id: str
    deployment_id: str
    decision: Literal[
        "pending",
        "align_runtime_to_registry",
        "align_registry_to_runtime",
        "keep_mismatch_for_investigation",
    ] = "pending"
    required_role: Literal["production_deployer"] = "production_deployer"
    reviewer: str | None = None
    reviewer_actor_id: str | None = None
    reviewer_roles: list[str] = Field(default_factory=list)
    reviewer_auth_source: str | None = None
    reviewer_issuer: str | None = None
    comment: str | None = None
    execution_required: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None


class ReconciliationResult(StrictSchema):
    """What actually happened, and what a re-observation afterwards proved.

    ``reconciliation_review_id`` and ``action`` are optional because a converged
    plan needs neither: nothing was approved and nothing was done. That is the
    ``already_converged`` outcome, and it is recorded rather than inferred.
    """

    reconciliation_result_id: str = Field(default_factory=new_id)
    reconciliation_plan_id: str
    reconciliation_review_id: str | None = None
    deployment_id: str
    action: ReconciliationDecision | None = None
    execution_status: Literal["not_started", "executed", "failed", "skipped"] = "not_started"
    convergence_status: Literal["verified", "not_verified", "still_mismatch"] = "not_verified"
    final_status: ReconciliationFinalStatus = "reconciliation_not_executed"
    pre_execution: DeploymentReconciliation | None = None
    post_execution: DeploymentReconciliation | None = None
    provider_job_id: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    executed_by: str | None = None
    executed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # ------------------------------------------------ Phase 9: convergence
    convergence_action: ConvergenceAction | None = None
    convergence_verdict: ConvergenceVerdict | None = None
    target_already_satisfied: bool = False
    skipped_reason: str | None = Field(default=None, max_length=200)
    conflict_diagnosis: ConflictDiagnosis | None = None
    desired_state_id: str | None = None


# `ProductionRollbackReview` (Phase 7) references the Phase 8 rollback preflight,
# which is declared further down. Resolve that forward reference once, explicitly.
ProductionRollbackReview.model_rebuild()


# ------------------------------------------------- provider wire-level results
#
# These two are the only structures a provider connection may return. They are
# deliberately narrow: no raw row payload beyond aggregates, no free text that a
# provider exception could smuggle into a public message.


class ProviderQueryResult(StrictSchema):
    """One read-only provider query. Failed queries carry a category, never text."""

    status: Literal["success", "failed", "timeout"] = "success"
    columns: list[str] = Field(default_factory=list, max_length=64)
    rows: list[dict] = Field(default_factory=list)
    truncated: bool = False
    provider_query_id: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    failure_category: str | None = None


class ProviderActivation(StrictSchema):
    """What the provider's own activation ledger says about one metric version.

    ``provider_job_id``/``provider_application_id`` stay ``None`` when the
    platform has no such concept. They are never synthesised locally.
    """

    deployment_id: str
    metric_version_id: str | None = None
    runtime_state: RuntimeState = "unknown"
    provider_job_id: str | None = None
    provider_application_id: str | None = None
    provider_query_id: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ================================================== Phase 9: desired state
#
# Three states, three questions, and they must never be collapsed into one field:
#
#   desired      - what a governance decision says the runtime should be
#   observed     - what the runtime reports right now
#   convergence  - how the three of them relate (converged / mismatch / unknown)
#
# Phase 8 could answer "registry = ?" and "runtime = ?". It could not answer
# "what still needs doing?", which is the only question that separates a real
# recovery from a pointless one.


class DesiredRuntimeState(StrictSchema):
    """Established by a governance decision. Never submitted by a client.

    §12/§63: a caller may only name a governed object id. The version itself is
    read from that object, so "desired = arbitrary" is not expressible here.
    """

    desired_state_id: str = Field(default_factory=new_id)
    metric_definition_id: str
    metric_key: Identifier
    environment_id: Identifier
    desired_metric_version_id: str
    desired_version: Version
    source: DesiredStateSource
    source_action_id: str
    source_deployment_id: str | None = None
    established_by: str
    established_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = Field(default=None, max_length=500)


class ExpectationEvaluationRun(StrictSchema):
    """Who looked for missing telemetry, when, and what they found.

    The run is first-class evidence: "nobody checked" and "we checked and it was
    fine" must not look the same in the audit trail.
    """

    evaluation_run_id: str = Field(default_factory=new_id)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evaluated_by: str
    policy_version: str = Field(default=DEFAULT_EXPECTATION_POLICY_VERSION, max_length=16)
    deployment_ids: list[str] = Field(default_factory=list)
    expectation_count: int = Field(default=0, ge=0)
    satisfied_count: int = Field(default=0, ge=0)
    due_soon_count: int = Field(default=0, ge=0)
    overdue_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    telemetry_missing_count: int = Field(default=0, ge=0)
    telemetry_late_count: int = Field(default=0, ge=0)
    alert_ids: list[str] = Field(default_factory=list)
    created_alert_ids: list[str] = Field(default_factory=list)
    statuses: list[MonitoringExpectationStatus] = Field(default_factory=list)


class TelemetrySourceHealth(StrictSchema):
    """Aggregate health of a *pipeline*, never a verdict about a metric.

    §29: a broken transport does not make the metric wrong, and this object says
    so explicitly instead of leaving the reader to infer it.
    """

    telemetry_source_id: str
    environment_id: Identifier
    source_system: NonEmptyText
    verified: bool
    synthetic: bool
    status: TelemetrySourceHealthStatus
    latest_observation_time: datetime | None = None
    latest_received_at: datetime | None = None
    latest_freshness: TelemetryFreshness | None = None
    snapshot_count: int = Field(default=0, ge=0)
    overdue_deployment_ids: list[str] = Field(default_factory=list)
    silent_deployment_ids: list[str] = Field(default_factory=list)
    detail: NonEmptyText
    metric_health_implied: Literal[False] = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ------------------------------------------------------------- notification


class NotificationResult(StrictSchema):
    status: Literal["delivered", "failed", "disabled"]
    sink: str
    provider_message_id: str | None = None
    failure_category: str | None = None
    detail: str | None = Field(default=None, max_length=500)


class NotificationDelivery(StrictSchema):
    """One attempt by one sink for one alert. Deliberately not a retry queue."""

    notification_delivery_id: str = Field(default_factory=new_id)
    alert_id: str
    deployment_id: str | None = None
    sink: str
    status: Literal["delivered", "failed", "disabled"]
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempted_by: str
    provider_message_id: str | None = None
    failure_category: str | None = None
    detail: str | None = Field(default=None, max_length=500)
    # True when this call returned an earlier successful delivery instead of
    # sending the same alert twice.
    replayed: bool = False


# ------------------------------------------------------------- enforcement

# The four facts an emergency override may never touch (§44/§71). Defined here,
# with the vocabulary, so the override model, the gate and the convergence helpers
# all refer to one list instead of three copies of it. Order is stable because the
# gate reports these names back to an operator.
PROTECTED_REQUIREMENTS: tuple[str, ...] = (
    "package_integrity",
    "artifact_integrity",
    "metric_version_identity",
    "actor_authentication",
)


class VerificationEnforcementPolicy(StrictSchema):
    """Whether a verification report merely describes or actually gates.

    §38: forcing every environment into `enforced` would be wrong for a staging
    cluster; forcing every environment into `report_only` would make the report
    decorative in production. The mode is resolved per environment.
    """

    version: Literal["1.0.0"] = DEFAULT_ENFORCEMENT_POLICY_VERSION
    mode: VerificationEnforcementMode = "report_only"


class EmergencyOverride(StrictSchema):
    """A time-boxed, attributable waiver of *verification evidence* - nothing else.

    §44: it can never waive package integrity, artifact integrity, metric version
    identity or actor authentication. ``waives`` is validated against the
    protected set at construction time.
    """

    emergency_override_id: str = Field(default_factory=new_id)
    action: GateAction
    deployment_id: str | None = None
    environment_id: Identifier | None = None
    reason: NonEmptyText
    requested_by: str
    approved_by: str
    waives: list[str] = Field(default_factory=list)
    expires_at: AwareDatetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("waives")
    @classmethod
    def _reject_protected_waivers(cls, value: list[str]) -> list[str]:
        """Refuse the override at construction rather than at use.

        A waiver that named a protected requirement would have to be silently
        dropped somewhere later, and "silently ignored" is indistinguishable from
        "honoured" in an audit trail. Failing to build it at all is the only
        behaviour that cannot be misread.
        """
        protected = sorted(set(value) & set(PROTECTED_REQUIREMENTS))
        if protected:
            raise ValueError(
                "an emergency override may not waive protected requirements: "
                + ", ".join(protected)
            )
        return sorted(set(value))

    def active_at(self, moment: datetime) -> bool:
        return moment < self.expires_at


class VerificationGateDecision(StrictSchema):
    """§46: the gate's answer, with the evidence it used and the gaps it found."""

    gate_decision_id: str = Field(default_factory=new_id)
    action: GateAction
    deployment_id: str
    environment_id: Identifier
    environment_kind: EnvironmentKind
    mode: VerificationEnforcementMode
    result: GateResult
    required_sections: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    integrity_requirements: list[str] = Field(default_factory=list)
    evidence_refs: dict = Field(default_factory=dict)
    report_id: str | None = None
    policy_version: str = Field(default=DEFAULT_ENFORCEMENT_POLICY_VERSION, max_length=16)
    override_id: str | None = None
    detail: NonEmptyText
    decided_by: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --------------------------------------------------------- closure + health


class ClosureMatrixEntry(StrictSchema):
    name: str
    status: ClosureStatus
    detail: NonEmptyText
    evidence: dict = Field(default_factory=dict)


class EnvironmentClosureMatrix(StrictSchema):
    """§54: which NOT VERIFIED items still stand between AIRI and real production.

    Completing Phase 9 code does not turn a row green. If the environment was not
    provided, the row stays NOT VERIFIED.
    """

    environment_id: Identifier | None = None
    environment_kind: EnvironmentKind | None = None
    entries: list[ClosureMatrixEntry] = Field(default_factory=list)
    verified_count: int = Field(default=0, ge=0)
    not_verified_count: int = Field(default=0, ge=0)
    not_applicable_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    open_items: list[str] = Field(default_factory=list)
    production_closed: bool = False
    detail: NonEmptyText
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionHealth(StrictSchema):
    """A read-only aggregate. §53: it never drives execution by itself."""

    deployment_id: str
    metric_definition_id: str
    environment_id: Identifier
    workflow_status: DeploymentStatus
    runtime_state: RuntimeState
    convergence_state: ConvergenceState
    health: ProductionHealthStatus
    reasons: list[str] = Field(default_factory=list)
    drives_execution: Literal[False] = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductionConvergence(StrictSchema):
    """The three-way view: what is wanted, what the registry says, what runs."""

    deployment_id: str
    metric_definition_id: str
    metric_version_id: str
    metric_key: Identifier
    environment_id: Identifier
    desired_state_id: str | None = None
    desired_metric_version_id: str | None = None
    desired_version: Version | None = None
    desired_source: DesiredStateSource | None = None
    registry_active_version_id: str | None = None
    registry_active_version: Version | None = None
    runtime_active_version_id: str | None = None
    runtime_active_version: Version | None = None
    runtime_state: RuntimeState
    runtime_identity: str | None = None
    environment_fingerprint_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    convergence_state: ConvergenceState
    target_already_satisfied: bool = False
    conflict: ConflictDiagnosis | None = None
    possible_actions: list[ConvergenceAction] = Field(default_factory=list)
    recommended_action: ConvergenceAction = "manual_investigation"
    rationale: NonEmptyText
    automatic_correction: Literal[False] = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

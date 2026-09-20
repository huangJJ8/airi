from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class ProductionEnvironmentProfileRow(Base):
    __tablename__ = "production_environment_profiles"
    environment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(2000))
    environment_kind: Mapped[str] = mapped_column(String(32))
    execution_mode: Mapped[str] = mapped_column(String(32))
    deployment_enabled: Mapped[bool] = mapped_column()
    verified: Mapped[bool] = mapped_column()
    synthetic_profile: Mapped[bool] = mapped_column()
    environment_validation_run_id: Mapped[str | None] = mapped_column(String(36))
    profile_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ProductionDeploymentPlanRow(Base):
    __tablename__ = "production_deployment_plans"
    deployment_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    package_id: Mapped[str] = mapped_column(String(36))
    target_environment: Mapped[str] = mapped_column(String(64))
    deployment_strategy: Mapped[str] = mapped_column(String(32))
    plan_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ProductionDeploymentRow(Base):
    __tablename__ = "production_deployments"
    deployment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(
        ForeignKey("metric_definitions.metric_definition_id")
    )
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_versions.metric_version_id"))
    release_id: Mapped[str] = mapped_column(ForeignKey("metric_releases.release_id"))
    environment_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40))
    deployment_package_hash: Mapped[str] = mapped_column(String(64))
    provider_job_id: Mapped[str | None] = mapped_column(String(128))
    runtime_mode: Mapped[str] = mapped_column(String(32))
    production_deployed: Mapped[bool] = mapped_column()
    runtime_binding_json: Mapped[dict | None] = mapped_column(JSON)
    deployment_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProductionDeploymentReviewRow(Base):
    __tablename__ = "production_deployment_reviews"
    deployment_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("production_deployments.deployment_id"), unique=True
    )
    metric_version_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    reviewer_actor_id: Mapped[str | None] = mapped_column(String(128))
    reviewer_roles: Mapped[list | None] = mapped_column(JSON)
    reviewer_auth_source: Mapped[str | None] = mapped_column(String(32))
    reviewer_issuer: Mapped[str | None] = mapped_column(String(256))
    comment: Mapped[str | None] = mapped_column(String(2000))
    package_hash: Mapped[str] = mapped_column(String(64))
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProductionRollbackReviewRow(Base):
    __tablename__ = "production_rollback_reviews"
    rollback_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("production_deployments.deployment_id"))
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    from_version_id: Mapped[str] = mapped_column(String(36))
    to_version_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(32))
    production_rollback_status: Mapped[str] = mapped_column(String(32))
    registry_reconciliation_status: Mapped[str] = mapped_column(String(32))
    runtime_verified: Mapped[bool] = mapped_column(default=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(128))
    provider_application_id: Mapped[str | None] = mapped_column(String(128))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    reviewer_actor_id: Mapped[str | None] = mapped_column(String(128))
    reviewer_roles: Mapped[list | None] = mapped_column(JSON)
    reviewer_auth_source: Mapped[str | None] = mapped_column(String(32))
    reviewer_issuer: Mapped[str | None] = mapped_column(String(256))
    preflight_json: Mapped[dict | None] = mapped_column(JSON)
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MonitoringSnapshotRow(Base):
    __tablename__ = "metric_monitoring_snapshots"
    __table_args__ = (UniqueConstraint("telemetry_source_id", "source_event_id"),)
    monitoring_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("production_deployments.deployment_id"))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    observation_time: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    execution_status: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column()
    psi: Mapped[float | None] = mapped_column()
    source_system: Mapped[str] = mapped_column(String(2000))
    source_event_id: Mapped[str] = mapped_column(String(2000))
    telemetry_source_id: Mapped[str | None] = mapped_column(String(64))
    telemetry_trust: Mapped[str] = mapped_column(String(32))
    telemetry_freshness: Mapped[str] = mapped_column(String(16))
    observation_lag_hours: Mapped[float | None] = mapped_column()
    monitoring_policy_version: Mapped[str] = mapped_column(String(16))
    publisher_actor_id: Mapped[str] = mapped_column(String(128))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MetricAlertRow(Base):
    __tablename__ = "metric_alerts"
    __table_args__ = (UniqueConstraint("dedup_key"),)
    alert_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("production_deployments.deployment_id"))
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    dedup_key: Mapped[str] = mapped_column(String(64))
    occurrences: Mapped[int] = mapped_column()
    recommended_action: Mapped[str] = mapped_column(String(32))
    monitoring_policy_version: Mapped[str] = mapped_column(String(16))
    alert_json: Mapped[dict] = mapped_column(JSON)
    alert_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MetricFeedbackEventRow(Base):
    __tablename__ = "metric_feedback_events"
    feedback_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str | None] = mapped_column(String(36))
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    recorded_by: Mapped[str] = mapped_column(String(128))
    event_json: Mapped[dict] = mapped_column(JSON)
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class FeedbackResearchRequestRow(Base):
    __tablename__ = "feedback_research_requests"
    research_request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    feedback_event_id: Mapped[str] = mapped_column(
        ForeignKey("metric_feedback_events.feedback_event_id")
    )
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str | None] = mapped_column(String(36))
    request_type: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(32))
    request_json: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


# ==================================================== Phase 8 persisted objects


class ProductionEnvironmentFingerprintRow(Base):
    """Observed runtime identity facts. Never contains credential material."""

    __tablename__ = "production_environment_fingerprints"
    environment_fingerprint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    environment_id: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str | None] = mapped_column(String(128))
    cluster_identifier: Mapped[str | None] = mapped_column(String(256))
    auth_mode: Mapped[str] = mapped_column(String(16))
    runtime_user: Mapped[str | None] = mapped_column(String(256))
    read_only_attested: Mapped[bool] = mapped_column()
    verified: Mapped[bool] = mapped_column()
    fingerprint_hash: Mapped[str] = mapped_column(String(64))
    fingerprint_json: Mapped[dict] = mapped_column(JSON)
    observed_by: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime)


class ProductionDeploymentEvidenceRow(Base):
    __tablename__ = "production_deployment_evidence"
    deployment_evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(64))
    deploy_status: Mapped[str] = mapped_column(String(32))
    status_confirmed: Mapped[bool] = mapped_column()
    production_deployed: Mapped[bool] = mapped_column()
    provider_job_id: Mapped[str | None] = mapped_column(String(128))
    provider_application_id: Mapped[str | None] = mapped_column(String(128))
    environment_fingerprint_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_json: Mapped[dict] = mapped_column(JSON)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime)


class TrustedTelemetrySourceRow(Base):
    __tablename__ = "trusted_telemetry_sources"
    telemetry_source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(2000))
    environment_id: Mapped[str] = mapped_column(String(64))
    auth_identity: Mapped[str] = mapped_column(String(256))
    schema_version: Mapped[str] = mapped_column(String(16))
    verified: Mapped[bool] = mapped_column()
    synthetic: Mapped[bool] = mapped_column()
    registered_by: Mapped[str] = mapped_column(String(128))
    source_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MonitoringExpectationRow(Base):
    __tablename__ = "monitoring_expectations"
    monitoring_expectation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("production_deployments.deployment_id"), unique=True
    )
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(64))
    telemetry_source_id: Mapped[str | None] = mapped_column(String(64))
    expected_interval_hours: Mapped[int] = mapped_column()
    grace_hours: Mapped[int] = mapped_column()
    created_by: Mapped[str] = mapped_column(String(128))
    expectation_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ReconciliationPlanRow(Base):
    __tablename__ = "reconciliation_plans"
    reconciliation_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(36))
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    verdict: Mapped[str] = mapped_column(String(16))
    recommended_action: Mapped[str] = mapped_column(String(32))
    plan_json: Mapped[dict] = mapped_column(JSON)
    plan_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ReconciliationReviewRow(Base):
    __tablename__ = "reconciliation_reviews"
    reconciliation_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reconciliation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_plans.reconciliation_plan_id")
    )
    deployment_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(40))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    reviewer_actor_id: Mapped[str | None] = mapped_column(String(128))
    reviewer_roles: Mapped[list | None] = mapped_column(JSON)
    reviewer_auth_source: Mapped[str | None] = mapped_column(String(32))
    reviewer_issuer: Mapped[str | None] = mapped_column(String(256))
    comment: Mapped[str | None] = mapped_column(String(2000))
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReconciliationResultRow(Base):
    __tablename__ = "reconciliation_results"
    reconciliation_result_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reconciliation_plan_id: Mapped[str] = mapped_column(String(36))
    # Nullable since Phase 9: a converged plan needs no review and no action, and
    # "nothing needed doing" is recorded rather than left blank.
    reconciliation_review_id: Mapped[str | None] = mapped_column(String(36))
    deployment_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str | None] = mapped_column(String(40))
    execution_status: Mapped[str] = mapped_column(String(32))
    convergence_status: Mapped[str] = mapped_column(String(32))
    final_status: Mapped[str] = mapped_column(String(48))
    result_json: Mapped[dict] = mapped_column(JSON)
    result_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ProductionVerificationReportRow(Base):
    __tablename__ = "production_verification_reports"
    production_verification_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(36))
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str] = mapped_column(String(36))
    overall: Mapped[str] = mapped_column(String(24))
    production_runtime_verified: Mapped[bool] = mapped_column()
    production_deployed: Mapped[bool] = mapped_column()
    report_json: Mapped[dict] = mapped_column(JSON)
    report_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime)


# ==================================================== Phase 9 persisted objects


class DesiredRuntimeStateRow(Base):
    """Append-only. The latest row for a (definition, environment) is the target."""

    __tablename__ = "desired_runtime_states"
    __table_args__ = (
        Index(
            "ix_desired_runtime_states_scope",
            "metric_definition_id",
            "environment_id",
            "established_at",
        ),
    )
    desired_state_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(64))
    desired_metric_version_id: Mapped[str] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(32))
    source_action_id: Mapped[str] = mapped_column(String(64))
    source_deployment_id: Mapped[str | None] = mapped_column(String(36))
    established_by: Mapped[str] = mapped_column(String(128))
    state_json: Mapped[dict] = mapped_column(JSON)
    state_hash: Mapped[str] = mapped_column(String(64))
    established_at: Mapped[datetime] = mapped_column(DateTime)


class ExpectationEvaluationRunRow(Base):
    __tablename__ = "expectation_evaluation_runs"
    evaluation_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluated_by: Mapped[str] = mapped_column(String(128))
    expectation_count: Mapped[int] = mapped_column()
    overdue_count: Mapped[int] = mapped_column()
    policy_version: Mapped[str] = mapped_column(String(16))
    run_json: Mapped[dict] = mapped_column(JSON)
    run_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime)


class NotificationDeliveryRow(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_notification_deliveries_alert", "alert_id", "sink"),)
    notification_delivery_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(36))
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    sink: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    provider_message_id: Mapped[str | None] = mapped_column(String(256))
    failure_category: Mapped[str | None] = mapped_column(String(64))
    delivery_json: Mapped[dict] = mapped_column(JSON)
    delivery_hash: Mapped[str] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(DateTime)


class VerificationGateDecisionRow(Base):
    __tablename__ = "verification_gate_decisions"
    gate_decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(32))
    deployment_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    result: Mapped[str] = mapped_column(String(24))
    override_id: Mapped[str | None] = mapped_column(String(36))
    decision_json: Mapped[dict] = mapped_column(JSON)
    decision_hash: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime)


class EmergencyOverrideRow(Base):
    """Persistence for the break-glass capability. Deliberately has no public route."""

    __tablename__ = "emergency_overrides"
    emergency_override_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(32))
    deployment_id: Mapped[str | None] = mapped_column(String(36))
    environment_id: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(128))
    override_json: Mapped[dict] = mapped_column(JSON)
    override_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)

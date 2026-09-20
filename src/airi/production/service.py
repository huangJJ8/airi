"""Production service layer.

    Active MetricVersion -> Deployment Plan -> Production Adapter -> Preflight
    -> Production Shadow -> Human Deployment Review -> Deploy -> Monitoring
    -> Alert -> Feedback -> Human Rollback -> Reconciliation -> Recovery

Every production precondition fails closed. The adapter boundary is the only
place that talks to a runtime, and the registry pointer is only ever moved
through :meth:`airi.registry.service.RegistryService.reconcile_active_version`.

Phase 8 adds the evidence layer: observed environment fingerprints, a
double-confirmed deployment record, telemetry attribution and freshness,
expectations, and a reconciliation that plans a recovery, has it approved and
then re-observes the runtime to check that it actually converged.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select, update

from airi.approvals.artifacts import canonical_hash
from airi.approvals.service import utcnow
from airi.core.exceptions import AIRIError
from airi.environments.persistence import EnvironmentValidationRow
from airi.production.adapters import (
    DisabledProductionAdapter,
    ProductionAdapter,
)
from airi.production.convergence import (
    build_convergence,
    convergence_verdict,
    diagnose_conflict,
    health_of,
    observed_runtime_state,
    three_way_state,
)
from airi.production.expectations import (
    evaluate_expectation,
    telemetry_finding_dedup_key,
    telemetry_source_health,
)
from airi.production.gate import (
    AUTO,
    GATE_ACTION_REQUIREMENTS,
    evaluate_verification_gate,
    resolve_enforcement_mode,
)
from airi.production.identity import (
    TRUSTED_AUTH_SOURCES,
    ActorIdentity,
    authorize,
    require_authenticated,
    require_trusted,
)
from airi.production.models import (
    DEPLOYMENT_TRANSITIONS,
    AlertDecision,
    ClosureMatrixEntry,
    DeploymentReconciliation,
    DesiredRuntimeState,
    EmergencyOverride,
    EnvironmentClosureMatrix,
    ExpectationEvaluationRun,
    FeedbackEvent,
    FeedbackRequest,
    FeedbackResearchDecision,
    FeedbackResearchRequest,
    MetricAlert,
    MonitoringBaseline,
    MonitoringDistribution,
    MonitoringExpectation,
    MonitoringExpectationRequest,
    MonitoringExpectationStatus,
    MonitoringSnapshot,
    MonitoringSnapshotRequest,
    NotificationDelivery,
    PreflightCheck,
    ProductionConvergence,
    ProductionDeployment,
    ProductionDeploymentEvidence,
    ProductionDeploymentPlan,
    ProductionDeploymentReview,
    ProductionEnvironmentFingerprint,
    ProductionEnvironmentProfile,
    ProductionEnvironmentRequest,
    ProductionHealth,
    ProductionPreflightResult,
    ProductionRollbackPreflight,
    ProductionRollbackReview,
    ProductionRuntimeBinding,
    ProductionRuntimeProbe,
    ProductionShadowResult,
    ProductionVerificationPolicy,
    ProductionVerificationReport,
    ReconciliationPlan,
    ReconciliationResult,
    ReconciliationReview,
    ReconciliationReviewCreate,
    ReconciliationReviewDecision,
    ResearchRequestCreate,
    TelemetrySourceHealth,
    TrustedTelemetrySource,
    TrustedTelemetrySourceRequest,
    VerificationGateDecision,
    deployment_evidence_hash,
    deployment_package_hash,
    feedback_event_hash,
    metric_alert_hash,
    monitoring_snapshot_content_hash,
    verification_report_hash,
)
from airi.production.monitoring import (
    MonitoringAssessor,
    finding_dedup_key,
    recommended_action,
    runtime_psi,
    telemetry_freshness,
)
from airi.production.notifications import DisabledNotificationSink
from airi.production.packaging import (
    PackageIntegrityError,
    build_package,
    package_failures,
)
from airi.production.persistence import (
    DesiredRuntimeStateRow,
    EmergencyOverrideRow,
    ExpectationEvaluationRunRow,
    FeedbackResearchRequestRow,
    MetricAlertRow,
    MetricFeedbackEventRow,
    MonitoringExpectationRow,
    MonitoringSnapshotRow,
    NotificationDeliveryRow,
    ProductionDeploymentEvidenceRow,
    ProductionDeploymentReviewRow,
    ProductionDeploymentRow,
    ProductionEnvironmentFingerprintRow,
    ProductionEnvironmentProfileRow,
    ProductionRollbackReviewRow,
    ProductionVerificationReportRow,
    ReconciliationPlanRow,
    ReconciliationResultRow,
    ReconciliationReviewRow,
    TrustedTelemetrySourceRow,
    VerificationGateDecisionRow,
)
from airi.production.probe import run_production_probe
from airi.production.reconciliation import (
    DECISION_TO_ACTION,
    build_reconciliation_plan,
    convergence_status,
)
from airi.production.verification import build_verification_report
from airi.refinement.transform import RefinementError
from airi.reflection.evidence import decode
from airi.registry.models import MetricReleaseEvent
from airi.registry.persistence import (
    MetricReleaseEventRow,
    MetricReleaseRow,
    MetricVersionRow,
    ReleaseReviewRow,
)
from airi.registry.service import RegistryError, RegistryService

logger = logging.getLogger("airi.production")

# Phase 7 audit entries live in the Phase 6 audit table so there is exactly one
# audit surface. Environment registration is not scoped to a metric, so it is
# recorded under a reserved scope instead of inventing a second audit stream.
ENVIRONMENT_SCOPE = "production_environment"
TELEMETRY_SCOPE = "telemetry_source"
RELEASABLE_STATUSES = ("approved", "active")
DEPLOYABLE_STATUSES = ("deployed", "monitoring")
TERMINAL_DEPLOYMENT_STATUSES = ("failed", "rolled_back")


def _jsonable(value):
    """Make an update value survive the strict JSON round-trip in ``decode``.

    ``_replace`` keeps the same code path as every other Phase 7 write, so a
    nested model or a datetime has to be normalised explicitly instead of being
    handed to ``json.dumps`` unchanged.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class ProductionError(RefinementError):
    """All production governance failures are 409 domain errors."""


class ProductionNotFound(ProductionError):
    status_code = 404


class _ProductionService:
    def __init__(self, session, state, request_id=None, adapter: ProductionAdapter | None = None):
        self.session, self.state, self.request_id = session, state, request_id
        self.registry = RegistryService(session, state)
        self.adapter = adapter if adapter is not None else self._resolve_adapter()

    def _resolve_adapter(self):
        factory = getattr(self.state, "production_adapter_factory", None)
        if factory is None:
            return DisabledProductionAdapter()
        return factory(self.session, self.state, self.request_id)

    def _connection(self):
        """The real-runtime connection used for fingerprinting and probing."""
        factory = getattr(self.state, "production_connection_factory", None)
        if factory is None:
            raise ProductionError("production_runtime_connection_not_configured")
        return factory()

    def _trust_boundary(self) -> str:
        settings = getattr(self.state, "settings", None)
        return getattr(settings, "production_identity_trust_boundary", "none") or "none"

    def _require_trusted_when_configured(self, identity: ActorIdentity) -> ActorIdentity:
        """Phase 8 fail-closed rule.

        Once a trust boundary is deployed, a production decision must come from
        it. A locally-injected identity is good enough only while no boundary
        exists - never alongside one.
        """
        if self._trust_boundary() != "none":
            require_trusted(identity)
        return identity

    def _decision_actors_trusted(self, *auth_sources) -> bool:
        """Whether every recorded decision came from a verified boundary."""
        sources = [source for source in auth_sources if source]
        return bool(sources) and all(source in TRUSTED_AUTH_SOURCES for source in sources)

    # ------------------------------------------- Phase 9: desired vs observed

    def _desired_state(self, metric_definition_id, environment_id):
        """The latest established target. Append-only, so the newest row wins."""
        row = self.session.scalar(
            select(DesiredRuntimeStateRow)
            .where(
                DesiredRuntimeStateRow.metric_definition_id == metric_definition_id,
                DesiredRuntimeStateRow.environment_id == environment_id,
            )
            .order_by(
                DesiredRuntimeStateRow.established_at.desc(),
                DesiredRuntimeStateRow.desired_state_id.desc(),
            )
        )
        return None if row is None else decode(DesiredRuntimeState, row.state_json)

    def _desired_state_by_id(self, desired_state_id):
        row = self.session.get(DesiredRuntimeStateRow, desired_state_id)
        return None if row is None else decode(DesiredRuntimeState, row.state_json)

    def _registry_active_version_id(self, metric_definition_id):
        """Read the pointer fresh. A compare-and-set is only meaningful against now."""
        return self.registry.definition_by_id(metric_definition_id).active_version_id

    def _desired_states(self, metric_definition_id=None):
        statement = select(DesiredRuntimeStateRow).order_by(DesiredRuntimeStateRow.established_at)
        if metric_definition_id:
            statement = statement.where(
                DesiredRuntimeStateRow.metric_definition_id == metric_definition_id
            )
        return [
            decode(DesiredRuntimeState, row.state_json) for row in self.session.scalars(statement)
        ]

    def _establish_desired_state(
        self,
        *,
        metric_definition_id,
        metric_key,
        environment_id,
        desired_version,
        source,
        source_action_id,
        identity,
        deployment_id=None,
        detail=None,
        event=True,
    ) -> DesiredRuntimeState:
        """Record where production should end up, derived from a governed object.

        §12/§63: the version is read from the deployment, the rollback review or the
        reconciliation review. No route accepts a version from a caller, so
        "desired = arbitrary" cannot be expressed.
        """
        state = DesiredRuntimeState(
            metric_definition_id=metric_definition_id,
            metric_key=metric_key,
            environment_id=environment_id,
            desired_metric_version_id=desired_version.metric_version_id,
            desired_version=desired_version.version,
            source=source,
            source_action_id=source_action_id,
            source_deployment_id=deployment_id,
            established_by=identity.actor_id,
            detail=detail,
        )
        self.session.add(
            DesiredRuntimeStateRow(
                desired_state_id=state.desired_state_id,
                metric_definition_id=state.metric_definition_id,
                environment_id=state.environment_id,
                desired_metric_version_id=state.desired_metric_version_id,
                source=state.source,
                source_action_id=state.source_action_id,
                source_deployment_id=state.source_deployment_id,
                established_by=state.established_by,
                state_json=state.model_dump(mode="json"),
                state_hash=canonical_hash(state),
                established_at=utcnow(),
            )
        )
        if event:
            self._event(
                metric_definition_id,
                "desired_runtime_state_established",
                actor=identity.actor_id,
                metric_version_id=state.desired_metric_version_id,
                metadata={
                    "desired_state_id": state.desired_state_id,
                    "environment_id": environment_id,
                    "source": source,
                    "source_action_id": source_action_id,
                    "desired_version": state.desired_version,
                },
            )
        return state

    # ------------------------------------------------------ Phase 9: the gate

    def _enforcement_mode(self, profile) -> str:
        configured = getattr(
            getattr(self.state, "settings", None), "production_verification_enforcement", AUTO
        )
        return resolve_enforcement_mode(profile=profile, configured=configured or AUTO)

    def _notification_sink(self):
        """Resolve the configured sink. A mock is never selectable by configuration."""
        injected = getattr(self.state, "notification_sink", None)
        if injected is not None:
            return injected
        settings = getattr(self.state, "settings", None)
        configured = getattr(settings, "production_notification_sink", "disabled")
        webhook = getattr(settings, "production_notification_webhook", "") or ""
        if configured == "dingtalk" and webhook:
            from airi.production.notifications import DingTalkNotificationSink

            return DingTalkNotificationSink(webhook)
        return DisabledNotificationSink()

    def _active_override(self, *, action, deployment_id, environment_id):
        """The most recent unexpired override that covers this action."""
        now = datetime.now(UTC)
        rows = self.session.scalars(
            select(EmergencyOverrideRow)
            .where(
                EmergencyOverrideRow.action == action,
                EmergencyOverrideRow.expires_at > now.replace(tzinfo=None),
            )
            .order_by(EmergencyOverrideRow.created_at.desc())
        )
        for row in rows:
            override = decode(EmergencyOverride, row.override_json)
            if override.deployment_id not in (None, deployment_id):
                continue
            if override.environment_id not in (None, environment_id):
                continue
            return override
        return None

    def _gate_report(self, deployment):
        """Build the report the gate reads. Read-only: the gate persists its decision."""
        environment = EnvironmentService(self.session, self.state, self.request_id)
        profile = environment.profile(deployment.environment_id)
        rollback_review = (
            self.rollback_review(deployment.rollback_review_id)
            if deployment.rollback_review_id
            else None
        )
        report = build_verification_report(
            deployment=deployment,
            profile=profile,
            fingerprint=environment.latest_fingerprint(deployment.environment_id),
            evidence=self.latest_evidence(deployment.deployment_id),
            telemetry_source=self._bound_telemetry_source(deployment.deployment_id),
            rollback_review=rollback_review,
            adapter_name=self.adapter.name,
            identity_trusted=self._trust_boundary() != "none",
            decision_actors_trusted=self._decision_actors_trusted(
                self._deployment_decision_source(deployment),
                None if rollback_review is None else rollback_review.reviewer_auth_source,
            ),
            policy=ProductionVerificationPolicy(),
        )
        return profile, report

    def _integrity_failures(self, deployment, profile) -> list[str]:
        """The protected set. These are checked regardless of mode and of override."""
        failures: list[str] = []
        environment = EnvironmentService(self.session, self.state, self.request_id)
        failures.extend(
            package_failures(
                self.registry,
                deployment.package,
                profile,
                environment.latest_fingerprint(deployment.environment_id),
            )
        )
        if deployment.package.metric_version_id != deployment.metric_version_id:
            failures.append("metric_version_identity")
        return sorted(set(failures))

    def _latest_report_id(self, deployment_id):
        row = self.session.scalar(
            select(ProductionVerificationReportRow)
            .where(ProductionVerificationReportRow.deployment_id == deployment_id)
            .order_by(ProductionVerificationReportRow.observed_at.desc())
        )
        return None if row is None else row.production_verification_run_id

    def _evaluate_gate(self, *, action, deployment, identity, persist=True):
        """§47: run the gate, then let the caller decide whether to act."""
        environment = EnvironmentService(self.session, self.state, self.request_id)
        profile = environment.profile(deployment.environment_id)
        _, report = self._gate_report(deployment)
        capabilities = {
            "rollback_capability": bool(self.adapter.supports_rollback),
            "recovery_capability": bool(getattr(self.adapter, "supports_recovery", False)),
            "authoritative_adapter": bool(self.adapter.authoritative),
        }
        override = self._active_override(
            action=action,
            deployment_id=deployment.deployment_id,
            environment_id=deployment.environment_id,
        )
        decision = evaluate_verification_gate(
            action=action,
            deployment_id=deployment.deployment_id,
            environment_id=deployment.environment_id,
            environment_kind=profile.environment_kind,
            profile=profile,
            report=report,
            capabilities=capabilities,
            integrity_failures=self._integrity_failures(deployment, profile),
            actor_authenticated=identity.authenticated,
            actor_trusted=identity.trusted,
            trust_boundary_configured=self._trust_boundary() != "none",
            configured_mode=getattr(
                getattr(self.state, "settings", None),
                "production_verification_enforcement",
                AUTO,
            )
            or AUTO,
            override=override,
            decided_by=identity.actor_id,
        )
        decision = self._replace(
            decision,
            evidence_refs={
                **decision.evidence_refs,
                "package_hash": deployment.package.content_hash,
                "metric_version_id": deployment.metric_version_id,
                "environment_fingerprint_hash": deployment.environment_fingerprint_hash,
                "required_sections": list(GATE_ACTION_REQUIREMENTS[action]),
            },
        )
        if persist:
            self.session.add(
                VerificationGateDecisionRow(
                    gate_decision_id=decision.gate_decision_id,
                    action=decision.action,
                    deployment_id=decision.deployment_id,
                    environment_id=decision.environment_id,
                    mode=decision.mode,
                    result=decision.result,
                    override_id=decision.override_id,
                    decision_json=decision.model_dump(mode="json"),
                    decision_hash=canonical_hash(decision),
                    decided_at=utcnow(),
                )
            )
            self._event(
                deployment.metric_definition_id,
                (
                    "verification_gate_passed"
                    if decision.result != "blocked"
                    else "verification_gate_blocked"
                ),
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "gate_decision_id": decision.gate_decision_id,
                    "action": decision.action,
                    "mode": decision.mode,
                    "result": decision.result,
                    "missing_requirements": decision.missing_requirements,
                },
            )
            self.session.flush()
        return decision

    # ------------------------------------------------- Phase 9: break glass

    def establish_emergency_override(
        self,
        *,
        action,
        reason,
        waives,
        expires_at,
        identity: ActorIdentity,
        deployment_id=None,
        environment_id=None,
    ) -> EmergencyOverride:
        """Record a time-boxed, attributable waiver of *verification evidence* (§44).

        Deliberately has no public route: break-glass is an operator action against
        the deployment, not a self-service API. A waiver that named a protected
        requirement is refused by :class:`EmergencyOverride` itself, so this method
        cannot be talked into weakening package integrity, artifact integrity,
        metric version identity or actor authentication.
        """
        require_authenticated(identity)
        override = EmergencyOverride(
            action=action,
            deployment_id=deployment_id,
            environment_id=environment_id,
            reason=reason,
            requested_by=identity.actor_id,
            approved_by=identity.actor_id,
            waives=sorted(set(waives)),
            expires_at=expires_at,
        )
        self.session.add(
            EmergencyOverrideRow(
                emergency_override_id=override.emergency_override_id,
                action=override.action,
                deployment_id=override.deployment_id,
                environment_id=override.environment_id,
                approved_by=override.approved_by,
                override_json=override.model_dump(mode="json"),
                override_hash=canonical_hash(override),
                expires_at=override.expires_at.astimezone(UTC).replace(tzinfo=None),
                created_at=utcnow(),
            )
        )
        self.session.flush()
        logger.info(
            "emergency_override_established",
            extra={
                "actor_id": identity.actor_id,
                "emergency_override_id": override.emergency_override_id,
                "action": override.action,
                "waives": override.waives,
                "request_id": self.request_id,
            },
        )
        return override

    def emergency_overrides(self, *, action=None):
        statement = select(EmergencyOverrideRow).order_by(EmergencyOverrideRow.created_at)
        if action:
            statement = statement.where(EmergencyOverrideRow.action == action)
        return [
            decode(EmergencyOverride, row.override_json) for row in self.session.scalars(statement)
        ]

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _replace(model, **updates):
        data = model.model_dump(mode="json")
        data.update({key: _jsonable(value) for key, value in updates.items()})
        return decode(type(model), data)

    def _event(
        self,
        metric_definition_id,
        event_type,
        *,
        actor,
        metric_version_id=None,
        release_id=None,
        metadata=None,
    ):
        self.session.add(
            MetricReleaseEventRow(
                event_id=str(uuid4()),
                metric_definition_id=metric_definition_id,
                metric_version_id=metric_version_id,
                release_id=release_id,
                event_type=event_type,
                actor=actor,
                metadata_json=metadata or {},
                created_at=utcnow(),
            )
        )
        self.session.flush()

    def events(self, metric_definition_id):
        rows = self.session.scalars(
            select(MetricReleaseEventRow)
            .where(MetricReleaseEventRow.metric_definition_id == metric_definition_id)
            .order_by(MetricReleaseEventRow.created_at)
        )
        return [
            MetricReleaseEvent(
                event_id=row.event_id,
                metric_definition_id=row.metric_definition_id,
                metric_version_id=row.metric_version_id,
                release_id=row.release_id,
                event_type=row.event_type,
                actor=row.actor,
                metadata=row.metadata_json,
                created_at=row.created_at.replace(tzinfo=UTC),
            )
            for row in rows
        ]


# ------------------------------------------------------------ environment gate


class EnvironmentService(_ProductionService):
    def register(self, payload: ProductionEnvironmentRequest, identity: ActorIdentity):
        authorize(identity, "register_production_environment")
        if payload.synthetic_profile and self.state.settings.environment == "production":
            raise ProductionError("synthetic_production_profile_forbidden")
        if payload.synthetic_profile and payload.execution_mode != "mock":
            raise ProductionError("synthetic_profile_requires_mock_execution")
        if self.session.get(ProductionEnvironmentProfileRow, payload.environment_id) is not None:
            raise ProductionError("production_environment_already_registered")
        verified, source = self._verification(payload)
        profile = ProductionEnvironmentProfile(
            environment_id=payload.environment_id,
            name=payload.name,
            environment_kind=payload.environment_kind,
            execution_mode=payload.execution_mode,
            deployment_enabled=payload.deployment_enabled,
            environment_validation_run_id=payload.environment_validation_run_id,
            verification_source=source,
            verified=verified,
            synthetic_profile=payload.synthetic_profile,
        )
        self.session.add(
            ProductionEnvironmentProfileRow(
                environment_id=profile.environment_id,
                name=profile.name,
                environment_kind=profile.environment_kind,
                execution_mode=profile.execution_mode,
                deployment_enabled=profile.deployment_enabled,
                verified=profile.verified,
                synthetic_profile=profile.synthetic_profile,
                environment_validation_run_id=profile.environment_validation_run_id,
                profile_json=profile.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        self._event(
            ENVIRONMENT_SCOPE,
            "production_environment_registered",
            actor=identity.actor_id,
            metadata={
                "environment_id": profile.environment_id,
                "environment_kind": profile.environment_kind,
                "execution_mode": profile.execution_mode,
                "verified": profile.verified,
                "verification_source": profile.verification_source,
                "deployment_enabled": profile.deployment_enabled,
            },
        )
        self.session.commit()
        logger.info(
            "production_environment_registered",
            extra={
                "actor_id": identity.actor_id,
                "environment_id": profile.environment_id,
                "status": "verified" if profile.verified else "not_verified",
                "request_id": self.request_id,
            },
        )
        return profile

    def _verification(self, payload: ProductionEnvironmentRequest):
        """`verified` is decided here, never accepted from a caller."""
        if payload.synthetic_profile:
            return payload.environment_kind == "production", "synthetic_profile"
        if payload.environment_validation_run_id is None:
            return False, "none"
        row = self.session.get(EnvironmentValidationRow, payload.environment_validation_run_id)
        if row is None:
            raise ProductionNotFound("environment_validation_not_found")
        return (
            payload.environment_kind == "production"
            and payload.execution_mode == "spark_production"
            and row.environment in {"spark_production", "production"}
            and row.status in {"passed", "passed_with_warnings"},
            "environment_validation_report",
        )

    def profile(self, environment_id):
        row = self.session.get(ProductionEnvironmentProfileRow, environment_id)
        if row is None:
            raise ProductionNotFound("production_environment_not_found")
        profile = decode(ProductionEnvironmentProfile, row.profile_json)
        if (
            row.environment_kind != profile.environment_kind
            or row.execution_mode != profile.execution_mode
            or bool(row.deployment_enabled) != profile.deployment_enabled
            or bool(row.verified) != profile.verified
            or row.environment_id != profile.environment_id
        ):
            raise ProductionError("production_environment_integrity_error")
        return profile

    def profiles(self):
        rows = self.session.scalars(
            select(ProductionEnvironmentProfileRow).order_by(
                ProductionEnvironmentProfileRow.environment_id
            )
        )
        return [self.profile(row.environment_id) for row in rows]

    def require_deployable(self, environment_id):
        profile = self.profile(environment_id)
        if not profile.deployable:
            raise ProductionError("production_environment_not_verified")
        if profile.execution_mode == "spark_production" and not profile.synthetic_profile:
            # Phase 8: a real production handover must be anchored to an observed
            # runtime. A configured host is not an environment.
            fingerprint = self.latest_fingerprint(environment_id)
            if fingerprint is None:
                raise ProductionError("production_environment_fingerprint_required")
            if not fingerprint.verified:
                raise ProductionError("production_environment_fingerprint_not_verified")
        return profile

    # ------------------------------------------------------- Phase 8: runtime

    def runtime_probe(self, environment_id, identity: ActorIdentity) -> ProductionRuntimeProbe:
        """Read-only probe of the production runtime. Never writes, never verifies."""
        require_authenticated(identity)
        profile = self.profile(environment_id)
        if profile.execution_mode != "spark_production":
            raise ProductionError("production_runtime_probe_not_applicable")
        connection = self._connection()
        try:
            return run_production_probe(connection, environment_id)
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    def fingerprint(self, environment_id, identity: ActorIdentity):
        """Observe and persist the runtime fingerprint. Fails closed when unreachable."""
        authorize(identity, "verify_production_environment")
        profile = self.profile(environment_id)
        if profile.execution_mode != "spark_production":
            raise ProductionError("environment_fingerprint_not_applicable")
        probe = self.runtime_probe(environment_id, identity)
        if not probe.reachable:
            raise ProductionError("production_runtime_unreachable")
        fingerprint = probe.fingerprint(
            environment_id=environment_id,
            read_only_attested=bool(
                getattr(self.state.settings, "spark_production_read_only_attested", False)
            ),
            observed_by=identity.actor_id,
        )
        self.session.add(
            ProductionEnvironmentFingerprintRow(
                environment_fingerprint_id=fingerprint.environment_fingerprint_id,
                environment_id=fingerprint.environment_id,
                engine_version=fingerprint.engine_version,
                cluster_identifier=fingerprint.cluster_identifier,
                auth_mode=fingerprint.auth_mode,
                runtime_user=fingerprint.runtime_user,
                read_only_attested=fingerprint.read_only_attested,
                verified=fingerprint.verified,
                fingerprint_hash=fingerprint.fingerprint_hash,
                fingerprint_json=fingerprint.model_dump(mode="json"),
                observed_by=fingerprint.observed_by,
                observed_at=fingerprint.observed_at.astimezone(UTC).replace(tzinfo=None),
            )
        )
        self._event(
            ENVIRONMENT_SCOPE,
            "production_environment_verified",
            actor=identity.actor_id,
            metadata={
                "environment_id": environment_id,
                "environment_fingerprint_id": fingerprint.environment_fingerprint_id,
                "fingerprint_hash": fingerprint.fingerprint_hash,
                "auth_mode": fingerprint.auth_mode,
                "read_only_attested": fingerprint.read_only_attested,
            },
        )
        if fingerprint.runtime_identity_verified:
            self._event(
                ENVIRONMENT_SCOPE,
                "runtime_identity_verified",
                actor=identity.actor_id,
                metadata={
                    "environment_id": environment_id,
                    "environment_fingerprint_id": fingerprint.environment_fingerprint_id,
                    "runtime_identity_present": True,
                },
            )
        self.session.commit()
        logger.info(
            "production_environment_verified",
            extra={
                "actor_id": identity.actor_id,
                "environment_id": environment_id,
                "environment_fingerprint_id": fingerprint.environment_fingerprint_id,
                "status": "verified" if fingerprint.verified else "not_verified",
                "request_id": self.request_id,
            },
        )
        return fingerprint

    def fingerprint_by_id(self, environment_fingerprint_id):
        row = self.session.get(ProductionEnvironmentFingerprintRow, environment_fingerprint_id)
        if row is None:
            raise ProductionNotFound("environment_fingerprint_not_found")
        fingerprint = decode(ProductionEnvironmentFingerprint, row.fingerprint_json)
        if row.fingerprint_hash != fingerprint.fingerprint_hash:
            raise ProductionError("production_deployment_evidence_changed")
        return fingerprint

    def latest_fingerprint(self, environment_id):
        row = self.session.scalar(
            select(ProductionEnvironmentFingerprintRow)
            .where(ProductionEnvironmentFingerprintRow.environment_id == environment_id)
            .order_by(ProductionEnvironmentFingerprintRow.observed_at.desc())
        )
        return None if row is None else self.fingerprint_by_id(row.environment_fingerprint_id)

    def fingerprints(self, environment_id):
        rows = self.session.scalars(
            select(ProductionEnvironmentFingerprintRow)
            .where(ProductionEnvironmentFingerprintRow.environment_id == environment_id)
            .order_by(ProductionEnvironmentFingerprintRow.observed_at)
        )
        return [self.fingerprint_by_id(row.environment_fingerprint_id) for row in rows]

    # -------------------------------------------- Phase 9: closure matrix

    def closure_matrix(self, environment_id: str | None = None) -> EnvironmentClosureMatrix:
        """§54: what still stands between AIRI and *this* environment being closed.

        Deliberately not a score. Shipping Phase 9 code changes nothing here: a row
        only turns green when the environment actually produced the evidence, and an
        item that cannot apply to a synthetic environment is ``not_applicable``
        rather than quietly counted as verified.

        ``blocked`` is reserved for an item that cannot even be attempted yet -
        for example telemetry trust on an environment that has no deployment to
        attribute telemetry to.
        """
        profile = None if environment_id is None else self.profile(environment_id)
        entries: list[ClosureMatrixEntry] = []

        def add(name, status, detail, **evidence):
            entries.append(
                ClosureMatrixEntry(name=name, status=status, detail=detail, evidence=evidence)
            )

        deployment_service = DeploymentService(self.session, self.state, self.request_id)
        deployments = [
            item
            for item in deployment_service.deployments()
            if environment_id is None or item.environment_id == environment_id
        ]
        synthetic = bool(profile is not None and profile.synthetic_profile)

        # 1. the environment itself
        if profile is None:
            add(
                "environment_profile",
                "not_verified",
                "no production environment has been registered",
            )
        elif profile.verified:
            add(
                "environment_profile",
                "verified",
                "the environment validation report was accepted",
                verification_source=profile.verification_source,
            )
        else:
            add(
                "environment_profile",
                "not_verified",
                "the environment is registered but its validation report was not accepted",
                verification_source=profile.verification_source,
            )

        # 2. an observed fingerprint, rather than a declared one
        fingerprint = None if profile is None else self.latest_fingerprint(profile.environment_id)
        if fingerprint is None:
            add(
                "environment_fingerprint",
                "blocked" if profile is None else "not_verified",
                "the runtime never answered for itself, so nothing about it is observed",
            )
        elif fingerprint.verified:
            add(
                "environment_fingerprint",
                "verified",
                "the runtime answered and the fingerprint came from a live probe",
                fingerprint_hash=fingerprint.fingerprint_hash,
                source=fingerprint.source,
            )
        else:
            add(
                "environment_fingerprint",
                "not_verified",
                "the fingerprint is synthetic: it was declared, not observed",
                source=fingerprint.source,
            )

        # 3. the identity boundary
        boundary = self._trust_boundary()
        if boundary == "none":
            add(
                "identity_trust_boundary",
                "not_verified",
                "no trusted identity boundary is configured; production decisions "
                "must come from a local injection",
            )
        else:
            add(
                "identity_trust_boundary",
                "verified",
                f"a {boundary} boundary vouches for production identities",
                trust_boundary=boundary,
            )

        # 4. the adapter
        if self.adapter.authoritative:
            add(
                "authoritative_adapter",
                "verified",
                f"the {self.adapter.name} adapter speaks to a real runtime",
                adapter=self.adapter.name,
            )
        elif synthetic:
            add(
                "authoritative_adapter",
                "not_applicable",
                "a synthetic environment cannot produce an authoritative runtime",
                adapter=self.adapter.name,
            )
        else:
            add(
                "authoritative_adapter",
                "not_verified",
                f"the {self.adapter.name} adapter is not authoritative",
                adapter=self.adapter.name,
            )

        # 5. rollback capability - declared, never assumed
        if self.adapter.supports_rollback:
            add(
                "rollback_capability",
                "verified",
                "the adapter declares a rollback channel",
                adapter=self.adapter.name,
            )
        else:
            add(
                "rollback_capability",
                "not_verified",
                "the adapter declares no rollback channel, so a rollback could not run",
                adapter=self.adapter.name,
            )

        # 6. a real production deployment
        real = [item for item in deployments if item.production_deployed]
        if real:
            add(
                "real_production_deployment",
                "verified",
                "at least one deployment is confirmed live on a verified runtime",
                deployment_ids=[item.deployment_id for item in real],
            )
        else:
            add(
                "real_production_deployment",
                "not_verified",
                "no deployment has been confirmed live on a verified production runtime",
                deployment_count=len(deployments),
            )

        # 7. trusted telemetry
        sources = [
            item
            for item in MonitoringService(
                self.session, self.state, self.request_id, self.adapter
            ).telemetry_sources(None if profile is None else profile.environment_id)
        ]
        trusted = [item for item in sources if item.verified and not item.synthetic]
        if trusted:
            add(
                "trusted_telemetry_source",
                "verified",
                "an attested, non-synthetic producer delivers this environment's telemetry",
                telemetry_source_ids=[item.telemetry_source_id for item in trusted],
            )
        elif not deployments:
            add(
                "trusted_telemetry_source",
                "blocked",
                "there is no deployment to attribute telemetry to yet",
            )
        else:
            add(
                "trusted_telemetry_source",
                "not_verified",
                "no attested producer has been registered for this environment",
                telemetry_source_count=len(sources),
            )

        # 8. a declared expectation - the thing that makes "missing" detectable
        monitoring = MonitoringService(self.session, self.state, self.request_id, self.adapter)
        declared = (
            monitoring.expectations([item.deployment_id for item in deployments])
            if deployments
            else []
        )
        if declared:
            add(
                "monitoring_expectation",
                "verified",
                "an expectation declares when telemetry should arrive",
                deployment_ids=[item.deployment_id for item in declared],
            )
        else:
            add(
                "monitoring_expectation",
                "not_verified",
                "no expectation is declared, so a silent pipeline cannot be detected",
            )

        # 9. somewhere for an alert to go
        sink = self._notification_sink()
        if sink.name == "disabled":
            add(
                "notification_sink",
                "not_verified",
                "no notification sink is configured; an alert would stay inside AIRI",
                sink=sink.name,
            )
        else:
            add(
                "notification_sink",
                "verified",
                f"alert delivery is configured for the {sink.name} sink",
                sink=sink.name,
            )

        counts = {status: 0 for status in ("verified", "not_verified", "not_applicable", "blocked")}
        for entry in entries:
            counts[entry.status] += 1
        open_items = [
            entry.name for entry in entries if entry.status in ("not_verified", "blocked")
        ]
        return EnvironmentClosureMatrix(
            environment_id=environment_id,
            environment_kind=None if profile is None else profile.environment_kind,
            entries=entries,
            verified_count=counts["verified"],
            not_verified_count=counts["not_verified"],
            not_applicable_count=counts["not_applicable"],
            blocked_count=counts["blocked"],
            open_items=open_items,
            # An environment is closed only when nothing is open. `not_applicable`
            # is not open, and it is not evidence either.
            production_closed=not open_items and profile is not None,
            detail=(
                "this environment is closed: every applicable item is verified"
                if not open_items and profile is not None
                else f"{len(open_items)} item(s) are not verified between AIRI and a "
                "closed production environment"
            ),
        )


# ------------------------------------------------------- deployment governance


class DeploymentService(_ProductionService):
    # ------------------------------------------------------------------ create

    def create(self, payload, identity: ActorIdentity):
        authorize(identity, "request_production_deployment")
        profile = EnvironmentService(self.session, self.state, self.request_id).require_deployable(
            payload.environment_id
        )
        release = self.registry.release(payload.release_id)
        if release.status not in RELEASABLE_STATUSES:
            raise ProductionError("release_not_approved_for_deployment")
        review_row = self.registry.session.scalar(
            select(ReleaseReviewRow).where(ReleaseReviewRow.release_id == release.release_id)
        )
        if review_row is None:
            raise ProductionError("release_review_not_approved")
        release_review = self.registry.release_review(review_row.release_review_id)
        if release_review.decision != "approved":
            raise ProductionError("release_review_not_approved")
        report = self.registry.validation_report(release.release_id)
        if report.status not in ("passed", "passed_with_warnings"):
            raise ProductionError("release_validation_failed")
        if report.release_eligibility != "eligible_for_release_review":
            raise ProductionError("release_not_eligible")
        version = self.registry.version_by_id(release.metric_version_id)
        existing = self.session.scalars(
            select(ProductionDeploymentRow).where(
                ProductionDeploymentRow.metric_version_id == version.metric_version_id,
                ProductionDeploymentRow.environment_id == profile.environment_id,
            )
        )
        for row in existing:
            if row.status not in TERMINAL_DEPLOYMENT_STATUSES:
                raise ProductionError("version_already_in_production_flow")
        package = build_package(
            registry=self.registry,
            profile=profile,
            release=release,
            version=version,
            review=release_review,
            report=report,
            fingerprint=EnvironmentService(
                self.session, self.state, self.request_id
            ).latest_fingerprint(profile.environment_id),
        )
        definition = self.registry.definition_by_id(version.metric_definition_id)
        expected = (
            self.registry.version_by_id(definition.active_version_id).version
            if definition.active_version_id
            else None
        )
        plan = ProductionDeploymentPlan(
            package_id=package.deployment_package_id,
            target_environment=profile.environment_id,
            expected_active_version=expected,
            deployment_strategy="shadow",
            preflight_checks=[
                "environment_verified",
                "environment_validation",
                "release_approved",
                "release_evidence",
                "package_integrity",
                "adapter_read_only",
                "shadow_passed",
            ],
            synthetic_data=package.synthetic_data,
        )
        deployment = ProductionDeployment(
            metric_definition_id=version.metric_definition_id,
            metric_version_id=version.metric_version_id,
            metric_key=version.metric_key,
            version=version.version,
            release_id=release.release_id,
            environment_id=profile.environment_id,
            package=package,
            plan=plan,
            runtime_mode=self.adapter.runtime_mode,
            real_environment_verified=(
                profile.execution_mode == "spark_production" and profile.verified
            ),
            environment_fingerprint_hash=package.environment_fingerprint_hash,
            synthetic_data=package.synthetic_data,
            requested_by=identity.actor_id,
        )
        self.session.add(
            ProductionDeploymentRow(
                deployment_id=deployment.deployment_id,
                metric_definition_id=deployment.metric_definition_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                environment_id=deployment.environment_id,
                status=deployment.status,
                deployment_package_hash=deployment.package.content_hash,
                provider_job_id=None,
                runtime_mode=deployment.runtime_mode,
                production_deployed=False,
                runtime_binding_json=None,
                deployment_json=deployment.model_dump(mode="json"),
                created_at=utcnow(),
                updated_at=utcnow(),
                deployed_at=None,
                rolled_back_at=None,
            )
        )
        self.session.commit()
        logger.info(
            "production_deployment_created",
            extra={
                "actor_id": identity.actor_id,
                "metric_definition_id": deployment.metric_definition_id,
                "metric_version_id": deployment.metric_version_id,
                "release_id": deployment.release_id,
                "production_deployment_id": deployment.deployment_id,
                "environment_id": deployment.environment_id,
                "status": deployment.status,
                "request_id": self.request_id,
            },
        )
        return deployment

    # ------------------------------------------------------------------- reads

    def deployment(self, deployment_id):
        row = self.session.get(ProductionDeploymentRow, deployment_id)
        if row is None:
            raise ProductionNotFound("production_deployment_not_found")
        deployment = decode(ProductionDeployment, row.deployment_json)
        if (
            row.status != deployment.status
            or row.metric_version_id != deployment.metric_version_id
            or row.deployment_package_hash != deployment.package.content_hash
            or bool(row.production_deployed) != deployment.production_deployed
            or row.runtime_mode != deployment.runtime_mode
            or row.environment_id != deployment.environment_id
        ):
            raise ProductionError("production_deployment_integrity_error")
        if deployment_package_hash(deployment.package) != deployment.package.content_hash:
            raise ProductionError("deployment_package_integrity_error")
        return deployment

    def deployments(self, metric_key=None):
        statement = select(ProductionDeploymentRow).order_by(ProductionDeploymentRow.created_at)
        rows = self.session.scalars(statement)
        deployments = [self.deployment(row.deployment_id) for row in rows]
        return (
            [item for item in deployments if item.metric_key == metric_key]
            if metric_key
            else deployments
        )

    def review(self, review_id):
        row = self.session.get(ProductionDeploymentReviewRow, review_id)
        if row is None:
            raise ProductionNotFound("production_deployment_review_not_found")
        review = decode(ProductionDeploymentReview, row.review_json)
        if row.decision != review.decision or row.package_hash != review.package_hash:
            raise ProductionError("production_deployment_evidence_changed")
        return review

    def rollback_review(self, review_id):
        row = self.session.get(ProductionRollbackReviewRow, review_id)
        if row is None:
            raise ProductionNotFound("production_rollback_review_not_found")
        review = decode(ProductionRollbackReview, row.review_json)
        if (
            row.decision != review.decision
            or row.production_rollback_status != review.production_rollback_status
            or bool(row.runtime_verified) != review.runtime_verified
        ):
            raise ProductionError("production_deployment_evidence_changed")
        return review

    def _write(self, deployment):
        row = self.session.get(ProductionDeploymentRow, deployment.deployment_id)
        row.status = deployment.status
        row.runtime_mode = deployment.runtime_mode
        row.production_deployed = deployment.production_deployed
        row.provider_job_id = (
            deployment.runtime_binding.provider_job_id if deployment.runtime_binding else None
        )
        row.runtime_binding_json = (
            deployment.runtime_binding.model_dump(mode="json")
            if deployment.runtime_binding
            else None
        )
        row.deployment_json = deployment.model_dump(mode="json")
        row.updated_at = utcnow()
        row.deployed_at = (
            deployment.deployed_at.astimezone(UTC).replace(tzinfo=None)
            if deployment.deployed_at
            else None
        )
        row.rolled_back_at = (
            deployment.rolled_back_at.astimezone(UTC).replace(tzinfo=None)
            if deployment.rolled_back_at
            else None
        )
        self.session.flush()

    def _transition(self, deployment, target):
        if target not in DEPLOYMENT_TRANSITIONS[deployment.status]:
            raise ProductionError("production_deployment_state_invalid")
        return self._replace(deployment, status=target, updated_at=datetime.now(UTC))

    # --------------------------------------------------------------- preflight

    def preflight(self, deployment_id, identity: ActorIdentity):
        authorize(identity, "request_production_deployment")
        deployment = self.deployment(deployment_id)
        if deployment.status != "created":
            raise ProductionError("production_deployment_not_preflightable")
        profile = EnvironmentService(self.session, self.state, self.request_id).profile(
            deployment.environment_id
        )
        checks, missing = [], []
        checks.extend(self._gate_checks(deployment, profile))
        validation = self.adapter.validate(deployment.package)
        for check in validation.checks:
            checks.append(check)
            if check.status == "not_verified":
                missing.append(f"adapter:{check.name}")
        # The fingerprint observed *now* is compared with the packaged one, so a
        # preflight against one cluster cannot authorise a deploy against another.
        checks.append(
            PreflightCheck(
                name="live_runtime_fingerprint",
                status="passed"
                if validation.environment_fingerprint_hash
                == deployment.package.environment_fingerprint_hash
                else "failed",
                detail=f"runtime={validation.environment_fingerprint_hash}; "
                f"package={deployment.package.environment_fingerprint_hash}",
            )
        )
        for item in checks:
            if item.name == "release_target_environment" and item.status == "not_verified":
                missing.append("production release environment not exercised")
        status = "failed" if any(item.status == "failed" for item in checks) else "passed"
        result = ProductionPreflightResult(
            deployment_id=deployment.deployment_id,
            status=status,
            checks=checks,
            missing_evidence=sorted(set(missing)),
        )
        deployment = self._replace(deployment, preflight=result)
        deployment = self._transition(
            deployment, "preflight_validated" if status == "passed" else "failed"
        )
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_preflight_validated",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "status": status,
                "missing_evidence": result.missing_evidence,
            },
        )
        self.session.commit()
        if status == "failed":
            raise ProductionError("production_preflight_failed")
        return deployment

    def _gate_checks(self, deployment, profile):
        """Every hard gate. Fail closed: no warning path may reach a deploy."""

        def check(name, ok, detail, not_verified=False):
            status = "passed" if ok else ("not_verified" if not_verified else "failed")
            return PreflightCheck(name=name, status=status, detail=detail)

        release = self.registry.release(deployment.release_id)
        report = self.registry.validation_report(deployment.release_id)
        failures = package_failures(self.registry, deployment.package, profile)
        version = self.registry.version_by_id(deployment.metric_version_id)
        fingerprint = EnvironmentService(
            self.session, self.state, self.request_id
        ).latest_fingerprint(profile.environment_id)
        return [
            check(
                "environment_verified",
                profile.deployable,
                f"kind={profile.environment_kind}; verified={profile.verified}; "
                f"deployment_enabled={profile.deployment_enabled}",
            ),
            check(
                "environment_validation",
                profile.verification_source != "none",
                f"verification_source={profile.verification_source}; "
                f"run={profile.environment_validation_run_id}",
                not_verified=profile.verification_source == "synthetic_profile",
            ),
            check(
                "environment_fingerprint",
                deployment.package.environment_fingerprint_hash is None
                or (
                    fingerprint is not None
                    and fingerprint.fingerprint_hash
                    == deployment.package.environment_fingerprint_hash
                ),
                "no runtime fingerprint is required for a synthetic environment"
                if deployment.package.environment_fingerprint_hash is None
                else f"packaged={deployment.package.environment_fingerprint_hash}; "
                f"observed={None if fingerprint is None else fingerprint.fingerprint_hash}",
            ),
            check(
                "adapter_authoritative",
                self.adapter.authoritative or profile.synthetic_profile,
                f"adapter={self.adapter.name}; authoritative={self.adapter.authoritative}",
            ),
            check(
                "release_approved",
                release.status in RELEASABLE_STATUSES,
                f"release_status={release.status}",
            ),
            check(
                "release_evidence",
                report.status in ("passed", "passed_with_warnings")
                and report.release_eligibility == "eligible_for_release_review",
                f"status={report.status}; eligibility={report.release_eligibility}",
            ),
            check(
                "package_integrity",
                not failures,
                "verified" if not failures else ",".join(failures),
            ),
            check(
                "metric_version_immutable",
                deployment.package.metric_version_content_hash == version.content_hash,
                "content hash matches the registry lineage",
            ),
            check(
                "read_only_preflight",
                profile.read_only_preflight,
                "preflight performs reads only",
            ),
            check(
                "release_target_environment",
                release.target_environment == "production",
                f"release_target_environment={release.target_environment}",
                not_verified=release.target_environment != "production",
            ),
        ]

    # ------------------------------------------------------------------ shadow

    def shadow(self, deployment_id, identity: ActorIdentity):
        authorize(identity, "request_production_deployment")
        deployment = self.deployment(deployment_id)
        if deployment.status != "preflight_validated":
            raise ProductionError("production_deployment_not_shadowable")
        profile = EnvironmentService(self.session, self.state, self.request_id).profile(
            deployment.environment_id
        )
        deployment = self._transition(deployment, "shadow_running")
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_shadow_started",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "write_business_output": False,
            },
        )
        self.session.commit()

        observation = self.adapter.observe(deployment.package)
        result = self._shadow_result(deployment, profile, observation)
        deployment = self._replace(deployment, shadow=result)
        deployment = self._transition(
            deployment, "failed" if result.status == "failed" else "shadow_validated"
        )
        if result.status != "failed":
            deployment = self._replace(
                deployment, monitoring_baseline=self._baseline(deployment, result)
            )
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_shadow_validated",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "status": result.status,
                "runtime_mode": result.runtime_mode,
                "write_business_output": False,
            },
        )
        self.session.commit()
        if result.status == "failed":
            raise ProductionError("production_shadow_failed")
        return deployment

    def _shadow_result(self, deployment, profile, observation) -> ProductionShadowResult:
        entity_count = observation.entity_count
        coverage = observation.non_null_count / entity_count if entity_count else None
        null_rate = observation.null_count / entity_count if entity_count else None
        duplicate_rate = (
            observation.duplicate_entities / observation.row_count
            if observation.row_count
            else None
        )
        findings, warnings = [], []
        if not observation.execution_success:
            findings.append(
                {
                    "type": "production_shadow_execution_failed",
                    "failure_category": observation.failure_category,
                }
            )
        if observation.duplicate_entities:
            findings.append({"type": "production_shadow_duplicate_entity"})
        if observation.execution_success and entity_count == 0:
            findings.append({"type": "production_shadow_no_entities"})
        if observation.execution_success and duplicate_rate is not None:
            warnings.append("duplicate_rate_recorded")
        status = (
            "failed"
            if any(
                item["type"]
                in ("production_shadow_execution_failed", "production_shadow_duplicate_entity")
                for item in findings
            )
            else "inconclusive"
            if any(item["type"] == "production_shadow_no_entities" for item in findings)
            else "passed"
        )
        return ProductionShadowResult(
            status=status,
            deployment_id=deployment.deployment_id,
            metric_version_id=deployment.metric_version_id,
            metric_key=deployment.metric_key,
            environment_id=deployment.environment_id,
            runtime_mode=self.adapter.runtime_mode,
            provider_query_id=observation.provider_query_id,
            provider_job_id=observation.provider_job_id,
            provider_application_id=observation.provider_application_id,
            runtime_identity=observation.runtime_identity,
            environment_fingerprint_hash=observation.environment_fingerprint_hash,
            columns=list(observation.columns),
            read_only_basis=observation.read_only_basis,
            execution_success=observation.execution_success,
            row_count=observation.row_count,
            entity_count=entity_count,
            coverage=coverage,
            null_rate=null_rate,
            duplicate_rate=duplicate_rate,
            latency_ms=observation.latency_ms,
            distribution=MonitoringDistribution(
                bins=list(observation.bins),
                total=sum(item.count for item in observation.bins),
            ),
            findings=findings,
            warnings=warnings,
            synthetic_data=observation.synthetic_data,
            observation_time=observation.observation_time,
        )

    def _baseline(self, deployment, result) -> MonitoringBaseline:
        return MonitoringBaseline(
            deployment_id=deployment.deployment_id,
            metric_version_id=deployment.metric_version_id,
            source="production_shadow",
            distribution=result.distribution,
            row_count=result.row_count,
            coverage=result.coverage,
            null_rate=result.null_rate,
            duplicate_rate=result.duplicate_rate,
            latency_ms=result.latency_ms,
            synthetic_data=result.synthetic_data,
            observation_time=result.observation_time,
        )

    # ------------------------------------------------------------ deploy review

    def create_review(self, deployment_id, identity: ActorIdentity):
        authorize(identity, "review_production_deployment")
        deployment = self.deployment(deployment_id)
        existing = self.session.scalar(
            select(ProductionDeploymentReviewRow).where(
                ProductionDeploymentReviewRow.deployment_id == deployment_id
            )
        )
        if deployment.status == "pending_deployment_review":
            previous = decode(ProductionDeploymentReview, existing.review_json)
            if previous.decision != "need_more_evidence":
                raise ProductionError("production_deployment_review_already_exists")
            review = self._replace(
                previous,
                decision="pending",
                reviewer=None,
                reviewer_actor_id=None,
                reviewer_roles=[],
                comment=None,
                reviewed_at=None,
                previous_decisions=[
                    *previous.previous_decisions,
                    {
                        "decision": previous.decision,
                        "reviewer": previous.reviewer,
                        "comment": previous.comment,
                    },
                ],
            )
            existing.decision, existing.reviewer, existing.comment, existing.reviewed_at = (
                "pending",
                None,
                None,
                None,
            )
            existing.review_json, existing.review_hash = (
                review.model_dump(mode="json"),
                canonical_hash(review),
            )
            self.session.commit()
            return review
        if deployment.status != "shadow_validated":
            raise ProductionError("production_deployment_not_ready_for_review")
        if deployment.shadow is None or deployment.shadow.status not in (
            "passed",
            "passed_with_warnings",
        ):
            raise ProductionError("production_shadow_not_passed")
        review = ProductionDeploymentReview(
            deployment_id=deployment.deployment_id,
            metric_version_id=deployment.metric_version_id,
            package_hash=deployment.package.content_hash,
        )
        self.session.add(
            ProductionDeploymentReviewRow(
                deployment_review_id=review.deployment_review_id,
                deployment_id=review.deployment_id,
                metric_version_id=review.metric_version_id,
                decision="pending",
                reviewer=None,
                reviewer_actor_id=None,
                comment=None,
                package_hash=review.package_hash,
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
                reviewed_at=None,
            )
        )
        deployment = self._replace(deployment, review_id=review.deployment_review_id)
        deployment = self._transition(deployment, "pending_deployment_review")
        self._write(deployment)
        self.session.commit()
        return review

    def decide_review(self, review_id, payload, identity: ActorIdentity):
        authorize(identity, "review_production_deployment")
        self._require_trusted_when_configured(identity)
        review = self.review(review_id)
        if review.decision != "pending":
            raise ProductionError("production_deployment_review_already_decided")
        deployment = self.deployment(review.deployment_id)
        if review.package_hash != deployment.package.content_hash:
            raise ProductionError("deployment_package_integrity_error")
        # The deploy gate is re-verified at decision time, not trusted from creation.
        profile = EnvironmentService(self.session, self.state, self.request_id).profile(
            deployment.environment_id
        )
        if not profile.deployable:
            raise ProductionError("production_environment_not_verified")
        if deployment.shadow is None or deployment.shadow.status not in (
            "passed",
            "passed_with_warnings",
        ):
            raise ProductionError("production_shadow_not_passed")
        updated = self._replace(
            review,
            decision=payload.decision,
            reviewer=identity.display_name,
            reviewer_actor_id=identity.actor_id,
            reviewer_roles=sorted(identity.roles),
            reviewer_auth_source=identity.auth_source,
            reviewer_issuer=identity.issuer,
            comment=payload.comment,
            reviewed_at=datetime.now(UTC),
        )
        changed = self.session.execute(
            update(ProductionDeploymentReviewRow)
            .where(
                ProductionDeploymentReviewRow.deployment_review_id == review_id,
                ProductionDeploymentReviewRow.decision == "pending",
            )
            .values(
                decision=payload.decision,
                reviewer=identity.display_name,
                reviewer_actor_id=identity.actor_id,
                reviewer_roles=sorted(identity.roles),
                reviewer_auth_source=identity.auth_source,
                reviewer_issuer=identity.issuer,
                comment=payload.comment,
                reviewed_at=utcnow(),
                review_json=updated.model_dump(mode="json"),
                review_hash=canonical_hash(updated),
            )
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise ProductionError("production_deployment_review_already_decided")
        if payload.decision == "approved":
            deployment = self._transition(deployment, "approved")
            self._write(deployment)
            self._event(
                deployment.metric_definition_id,
                "production_deployment_approved",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "deployment_review_id": review_id,
                    "reviewer_roles": sorted(identity.roles),
                },
            )
            # Phase 9: approving a deployment is what establishes the desired state.
            # No caller ever supplies the version - it is read from the deployment.
            self._establish_desired_state(
                metric_definition_id=deployment.metric_definition_id,
                metric_key=deployment.metric_key,
                environment_id=deployment.environment_id,
                desired_version=self.registry.version_by_id(deployment.metric_version_id),
                source="deployment",
                source_action_id=review_id,
                deployment_id=deployment.deployment_id,
                identity=identity,
                detail="established by the approved production deployment review",
            )
        elif payload.decision == "rejected":
            deployment = self._transition(deployment, "failed")
            self._write(deployment)
        self.session.commit()
        logger.info(
            "production_deployment_review_decided",
            extra={
                "actor_id": identity.actor_id,
                "production_deployment_id": deployment.deployment_id,
                "deployment_review_id": review_id,
                "status": payload.decision,
                "request_id": self.request_id,
            },
        )
        return updated

    # ------------------------------------------------------------------ deploy

    def deploy(self, deployment_id, identity: ActorIdentity):
        authorize(identity, "request_production_deployment")
        deployment = self.deployment(deployment_id)
        if deployment.status == "deployed" or deployment.status == "monitoring":
            return deployment
        if deployment.status != "approved":
            raise ProductionError("production_deployment_not_approved")
        if deployment.review_id is None:
            raise ProductionError("production_deployment_review_missing")
        review = self.review(deployment.review_id)
        if review.decision != "approved":
            raise ProductionError("production_deployment_review_not_approved")
        environment = EnvironmentService(self.session, self.state, self.request_id)
        profile = environment.require_deployable(deployment.environment_id)
        if package_failures(
            self.registry,
            deployment.package,
            profile,
            environment.latest_fingerprint(deployment.environment_id),
        ):
            raise ProductionError("deployment_package_integrity_error")
        if deployment.shadow is None or deployment.shadow.status not in (
            "passed",
            "passed_with_warnings",
        ):
            raise ProductionError("production_shadow_not_passed")

        # §47: the gate runs *before* the provider call, not after it. A report that
        # can only be read once production has already been touched is documentation.
        gate = self._evaluate_gate(action="deploy", deployment=deployment, identity=identity)
        if gate.result == "blocked":
            self.session.commit()
            raise ProductionError("production_verification_gate_blocked")

        result = self.adapter.deploy(deployment.package, deployment.deployment_id)
        # §19: the provider's *own* status is read back. A deploy response that the
        # runtime does not corroborate is not a deployment, however it is worded.
        status = (
            self.adapter.status(deployment.deployment_id) if result.status == "deployed" else None
        )
        status_confirmed = bool(
            status is not None
            and status.runtime_state == "active"
            and status.active_version_id == deployment.metric_version_id
        )
        if result.status == "deployed" and self.adapter.authoritative and not status_confirmed:
            self._record_deployment_evidence(
                deployment=deployment,
                result=result,
                status=status,
                status_confirmed=False,
            )
            deployment = self._replace(
                deployment,
                missing_evidence=sorted(
                    {*deployment.missing_evidence, "runtime status did not confirm the deploy"}
                ),
            )
            deployment = self._transition(deployment, "failed")
            self._write(deployment)
            self.session.commit()
            raise ProductionError("production_deployment_status_unconfirmed")

        binding = ProductionRuntimeBinding(
            deployment_id=deployment.deployment_id,
            metric_version_id=deployment.metric_version_id,
            environment_id=deployment.environment_id,
            provider_job_id=result.provider_job_id,
            provider_query_id=result.provider_query_id,
            provider_application_id=result.provider_application_id,
            schedule_id=result.schedule_id,
            runtime_identity=result.runtime_identity,
            runtime_identity_verified=result.runtime_identity_verified,
            environment_fingerprint_hash=result.environment_fingerprint_hash,
            activated_at=datetime.now(UTC) if result.status == "deployed" else None,
        )
        missing = list(deployment.missing_evidence)
        if result.runtime_identity_verified:
            missing = [item for item in missing if "runtime identity" not in item]
        else:
            missing.append("real production runtime identity not verified")
        if self.adapter.runtime_mode == "mock":
            missing.append("synthetic mock provider; no real production runtime")
        if deployment.environment_fingerprint_hash is None:
            missing.append("no verified production environment fingerprint")
        production_deployed = bool(
            self.adapter.authoritative
            and result.status == "deployed"
            and result.runtime_identity_verified
            and status_confirmed
            and deployment.environment_fingerprint_hash is not None
        )
        if result.status == "deployed":
            deployment = self._replace(
                deployment,
                runtime_binding=binding,
                runtime_identity=result.runtime_identity,
                runtime_identity_verified=result.runtime_identity_verified,
                production_deployed=production_deployed,
                missing_evidence=sorted(set(missing)),
                deployed_at=datetime.now(UTC),
            )
            deployment = self._transition(deployment, "deployed")
        else:
            deployment = self._replace(
                deployment,
                runtime_binding=binding,
                missing_evidence=sorted(set(missing)),
            )
            deployment = self._transition(deployment, "failed")
        self._write(deployment)
        if result.status == "deployed":
            evidence = self._record_deployment_evidence(
                deployment=deployment,
                result=result,
                status=status,
                status_confirmed=status_confirmed,
            )
            deployment = self._replace(
                deployment, deployment_evidence_id=evidence.deployment_evidence_id
            )
            self._write(deployment)
            self._event(
                deployment.metric_definition_id,
                "production_deployed",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "runtime_mode": self.adapter.runtime_mode,
                    "production_deployed": production_deployed,
                    "provider_job_id": result.provider_job_id,
                    "provider_application_id": result.provider_application_id,
                    "runtime_identity_verified": result.runtime_identity_verified,
                    "status_confirmed": status_confirmed,
                },
            )
        self.session.commit()
        logger.info(
            "production_deploy_finished",
            extra={
                "actor_id": identity.actor_id,
                "metric_version_id": deployment.metric_version_id,
                "production_deployment_id": deployment.deployment_id,
                "status": deployment.status,
                "provider_query_id": result.provider_query_id,
                "deployment_evidence_id": deployment.deployment_evidence_id,
                "request_id": self.request_id,
            },
        )
        if result.status != "deployed":
            raise ProductionError("production_deployment_failed")
        return deployment

    def _record_deployment_evidence(
        self, *, deployment, result, status, status_confirmed: bool
    ) -> ProductionDeploymentEvidence:
        evidence = ProductionDeploymentEvidence(
            deployment_id=deployment.deployment_id,
            metric_version_id=deployment.metric_version_id,
            metric_key=deployment.metric_key,
            version=deployment.version,
            environment_id=deployment.environment_id,
            package_hash=deployment.package.content_hash,
            environment_fingerprint_hash=result.environment_fingerprint_hash,
            adapter=self.adapter.name,
            authoritative=bool(self.adapter.authoritative),
            deploy_status=result.status,
            runtime_identity=result.runtime_identity,
            runtime_identity_verified=result.runtime_identity_verified,
            provider_job_id=result.provider_job_id,
            provider_application_id=result.provider_application_id,
            provider_query_id=result.provider_query_id,
            status_confirmed=status_confirmed,
            status_runtime_state=None if status is None else status.runtime_state,
            status_active_version_id=None if status is None else status.active_version_id,
            production_deployed=bool(
                deployment.production_deployed
                or (
                    self.adapter.authoritative
                    and result.status == "deployed"
                    and result.runtime_identity_verified
                    and status_confirmed
                )
            ),
            detail=result.detail,
            synthetic=self.adapter.runtime_mode == "mock",
            deployed_at=deployment.deployed_at,
            evidence_hash="0" * 64,
        )
        evidence = evidence.model_copy(update={"evidence_hash": deployment_evidence_hash(evidence)})
        self.session.add(
            ProductionDeploymentEvidenceRow(
                deployment_evidence_id=evidence.deployment_evidence_id,
                deployment_id=evidence.deployment_id,
                metric_version_id=evidence.metric_version_id,
                environment_id=evidence.environment_id,
                deploy_status=evidence.deploy_status,
                status_confirmed=evidence.status_confirmed,
                production_deployed=evidence.production_deployed,
                provider_job_id=evidence.provider_job_id,
                provider_application_id=evidence.provider_application_id,
                environment_fingerprint_hash=evidence.environment_fingerprint_hash,
                evidence_json=evidence.model_dump(mode="json"),
                evidence_hash=evidence.evidence_hash,
                observed_at=utcnow(),
            )
        )
        self._event(
            deployment.metric_definition_id,
            "production_deployment_evidence_recorded",
            actor="production-service",
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "deployment_evidence_id": evidence.deployment_evidence_id,
                "deploy_status": evidence.deploy_status,
                "status_confirmed": evidence.status_confirmed,
                "authoritative": evidence.authoritative,
            },
        )
        return evidence

    def evidence_records(self, deployment_id):
        rows = self.session.scalars(
            select(ProductionDeploymentEvidenceRow)
            .where(ProductionDeploymentEvidenceRow.deployment_id == deployment_id)
            .order_by(ProductionDeploymentEvidenceRow.observed_at)
        )
        return [self.deployment_evidence(row.deployment_evidence_id) for row in rows]

    def deployment_evidence(self, deployment_evidence_id):
        row = self.session.get(ProductionDeploymentEvidenceRow, deployment_evidence_id)
        if row is None:
            raise ProductionNotFound("deployment_evidence_not_found")
        evidence = decode(ProductionDeploymentEvidence, row.evidence_json)
        if (
            row.evidence_hash != evidence.evidence_hash
            or row.deploy_status != evidence.deploy_status
            or bool(row.production_deployed) != evidence.production_deployed
        ):
            raise ProductionError("production_deployment_evidence_changed")
        if deployment_evidence_hash(evidence) != evidence.evidence_hash:
            raise ProductionError("production_deployment_evidence_changed")
        return evidence

    def latest_evidence(self, deployment_id):
        rows = self.evidence_records(deployment_id)
        return rows[-1] if rows else None

    # ---------------------------------------------------------------- rollback

    def _release_for_version(self, metric_version_id):
        row = self.session.scalar(
            select(MetricReleaseRow)
            .where(MetricReleaseRow.metric_version_id == metric_version_id)
            .order_by(MetricReleaseRow.created_at.desc())
        )
        return None if row is None else self.registry.release(row.release_id)

    def _rollback_preflight(self, deployment, target) -> ProductionRollbackPreflight:
        """§48: a real rollback is checked before it is put to a human.

        Every check is a fact we can verify locally. The runtime's own support for
        a rollback is the adapter's declaration, not an assumption: an adapter with
        no control channel cannot be asked to roll anything back.
        """
        release = self._release_for_version(target.metric_version_id)
        environment = EnvironmentService(self.session, self.state, self.request_id)
        fingerprint = environment.latest_fingerprint(deployment.environment_id)
        checks = [
            PreflightCheck(
                name="target_version_exists",
                status="passed",
                detail=f"target_version={target.version}",
            ),
            PreflightCheck(
                name="target_version_released",
                status="passed"
                if release is not None and release.status in ("approved", "active", "rolled_back")
                else "not_verified",
                detail=(
                    f"release_status={release.status}"
                    if release is not None
                    else "the target version has no release record"
                ),
            ),
            PreflightCheck(
                name="target_targets_production",
                status="passed"
                if release is not None and release.target_environment == "production"
                else "not_verified",
                detail=(
                    f"target_environment={release.target_environment}"
                    if release is not None
                    else "no release record"
                ),
            ),
            PreflightCheck(
                name="environment_unchanged",
                status="passed"
                if deployment.environment_fingerprint_hash is None
                or (
                    fingerprint is not None
                    and fingerprint.fingerprint_hash == deployment.environment_fingerprint_hash
                )
                else "failed",
                detail=(
                    "the runtime fingerprint still matches the deployment package"
                    if fingerprint is not None
                    else "no runtime fingerprint is recorded for this environment"
                ),
            ),
            PreflightCheck(
                name="runtime_supports_rollback",
                status="passed" if self.adapter.supports_rollback else "not_verified",
                detail=(
                    f"adapter={self.adapter.name}; "
                    f"supports_rollback={self.adapter.supports_rollback}"
                ),
            ),
            PreflightCheck(
                name="human_approval_required",
                status="passed",
                detail="a rollback is only executed after a rollback_approver decision",
            ),
        ]
        missing = [f"rollback:{check.name}" for check in checks if check.status == "not_verified"]
        return ProductionRollbackPreflight(
            deployment_id=deployment.deployment_id,
            target_version_id=target.metric_version_id,
            target_version=target.version,
            status="failed" if any(item.status == "failed" for item in checks) else "passed",
            checks=checks,
            missing_evidence=sorted(set(missing)),
        )

    def request_rollback(self, deployment_id, payload, identity: ActorIdentity):
        require_authenticated(identity)
        deployment = self.deployment(deployment_id)
        if deployment.status not in ("deployed", "monitoring"):
            raise ProductionError("production_deployment_not_rollbackable")
        definition = self.registry.definition_by_id(deployment.metric_definition_id)
        target = self.registry.version(deployment.metric_key, payload.target_version)
        if target.metric_version_id == deployment.metric_version_id:
            raise ProductionError("rollback_target_already_active")
        preflight = self._rollback_preflight(deployment, target)
        if preflight.status != "passed":
            self._event(
                deployment.metric_definition_id,
                "production_rollback_preflight_validated",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "status": "failed",
                    "missing_evidence": preflight.missing_evidence,
                },
            )
            self.session.commit()
            raise ProductionError("production_rollback_preflight_failed")
        review = ProductionRollbackReview(
            deployment_id=deployment.deployment_id,
            metric_definition_id=deployment.metric_definition_id,
            metric_key=deployment.metric_key,
            from_version_id=deployment.metric_version_id,
            from_version=deployment.version,
            to_version_id=target.metric_version_id,
            to_version=target.version,
            reason=payload.reason,
            alert_ids=list(payload.alert_ids),
            preflight=preflight,
        )
        self.session.add(
            ProductionRollbackReviewRow(
                rollback_review_id=review.rollback_review_id,
                deployment_id=review.deployment_id,
                metric_definition_id=review.metric_definition_id,
                from_version_id=review.from_version_id,
                to_version_id=review.to_version_id,
                decision="pending",
                production_rollback_status="not_started",
                registry_reconciliation_status="pending",
                runtime_verified=False,
                provider_job_id=None,
                provider_application_id=None,
                reviewer=None,
                reviewer_actor_id=None,
                reviewer_roles=None,
                reviewer_auth_source=None,
                reviewer_issuer=None,
                preflight_json=preflight.model_dump(mode="json"),
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
                reviewed_at=None,
            )
        )
        deployment = self._replace(deployment, rollback_review_id=review.rollback_review_id)
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_rollback_preflight_validated",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "rollback_review_id": review.rollback_review_id,
                "status": "passed",
                "missing_evidence": preflight.missing_evidence,
            },
        )
        self.session.commit()
        logger.info(
            "production_rollback_requested",
            extra={
                "actor_id": identity.actor_id,
                "metric_version_id": deployment.metric_version_id,
                "production_deployment_id": deployment.deployment_id,
                "request_id": self.request_id,
                "target_version": definition.metric_key and target.version,
            },
        )
        return review

    def decide_rollback(self, review_id, payload, identity: ActorIdentity):
        """Two phases with named partial failure.

        The provider call cannot join the registry transaction, so it never does.
        If the provider stops the version but the registry pointer move fails, the
        result is `rollback_reconciliation_required`, never a silent success.
        """
        authorize(identity, "approve_production_rollback")
        self._require_trusted_when_configured(identity)
        review = self.rollback_review(review_id)
        if review.decision != "pending":
            raise ProductionError("production_rollback_already_decided")
        deployment = self.deployment(review.deployment_id)
        updated = self._replace(
            review,
            decision=payload.decision,
            reviewer=identity.display_name,
            reviewer_actor_id=identity.actor_id,
            reviewer_roles=sorted(identity.roles),
            reviewer_auth_source=identity.auth_source,
            reviewer_issuer=identity.issuer,
            comment=payload.comment,
            reviewed_at=datetime.now(UTC),
        )
        changed = self.session.execute(
            update(ProductionRollbackReviewRow)
            .where(
                ProductionRollbackReviewRow.rollback_review_id == review_id,
                ProductionRollbackReviewRow.decision == "pending",
            )
            .values(
                decision=payload.decision,
                reviewer=identity.display_name,
                reviewer_actor_id=identity.actor_id,
                reviewer_roles=sorted(identity.roles),
                reviewer_auth_source=identity.auth_source,
                reviewer_issuer=identity.issuer,
                reviewed_at=utcnow(),
                review_json=updated.model_dump(mode="json"),
                review_hash=canonical_hash(updated),
            )
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise ProductionError("production_rollback_already_decided")
        self.session.commit()
        if payload.decision == "rejected":
            logger.info(
                "production_rollback_rejected",
                extra={
                    "actor_id": identity.actor_id,
                    "production_deployment_id": deployment.deployment_id,
                    "rollback_review_id": review_id,
                    "status": "rejected",
                    "request_id": self.request_id,
                },
            )
            return updated

        # §41/§47: the gate is action-aware and runs before the provider call.
        # A rollback is what happens when production is already broken, so it is
        # deliberately not gated on the telemetry pipeline it may be rescuing.
        gate = self._evaluate_gate(action="rollback", deployment=deployment, identity=identity)
        if gate.result == "blocked":
            self.session.commit()
            raise ProductionError("production_verification_gate_blocked")

        updated = self._replace(updated, production_rollback_status="started")
        self._write_rollback_review(updated)
        self._event(
            deployment.metric_definition_id,
            "rollback_started",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "rollback_review_id": review_id,
                "from_version": review.from_version,
                "to_version": review.to_version,
            },
        )
        self.session.commit()

        # Phase 1: the provider. No database transaction is open here.
        result = self.adapter.rollback(deployment.deployment_id, review.to_version_id)
        if result.status != "rolled_back":
            updated = self._replace(
                updated,
                production_rollback_status="failed",
                registry_reconciliation_status="skipped",
                runtime_verified=False,
            )
            self._write_rollback_review(updated)
            deployment = self._transition(deployment, "failed")
            deployment = self._replace(deployment, rolled_back_at=datetime.now(UTC))
            self._write(deployment)
            self.session.commit()
            raise ProductionError("production_rollback_failed")

        # Phase 1b: the runtime is re-read, so the review records verified
        # provider facts rather than the provider's own success message.
        runtime_status = self.adapter.status(deployment.deployment_id)
        runtime_verified = bool(
            runtime_status.runtime_state == "active"
            and runtime_status.active_version_id == review.to_version_id
        )

        # Phase 2: the registry pointer, in its own transaction.
        #
        # §8: observe before acting. A compare-and-set can fail *because the goal
        # was already met* - a human reconciliation may already have moved the
        # pointer onto this very version. That is not a conflict, it is a no-op
        # with a verified outcome. Reporting `rollback_reconciliation_required`
        # for it was the Phase 8 bug that Phase 9 exists to fix.
        actual = self._registry_active_version_id(review.metric_definition_id)
        registry_status = "reconciled"
        diagnosis = None
        if actual == review.to_version_id:
            registry_status = "already_satisfied"
            diagnosis = diagnose_conflict(
                expected=review.from_version_id,
                actual=actual,
                desired=review.to_version_id,
            )
        else:
            try:
                self.registry.reconcile_active_version(
                    review.metric_definition_id,
                    from_version_id=review.from_version_id,
                    to_version_id=review.to_version_id,
                    actor=identity.actor_id,
                    reason=review.reason,
                )
            except (RegistryError, AIRIError) as exc:
                self.session.rollback()
                latest = self._registry_active_version_id(review.metric_definition_id)
                diagnosis = diagnose_conflict(
                    expected=review.from_version_id,
                    actual=latest,
                    desired=review.to_version_id,
                    runtime_state=runtime_status.runtime_state,
                )
                if diagnosis.category != "target_already_satisfied":
                    return self._named_rollback_reconciliation_failure(
                        review=review,
                        review_id=review_id,
                        identity=identity,
                        runtime_verified=runtime_verified,
                        result=result,
                        diagnosis=diagnosis,
                        exc=exc,
                    )
                registry_status = "already_satisfied"

        updated = self._replace(
            updated,
            production_rollback_status="succeeded",
            provider_job_id=result.provider_job_id,
            provider_application_id=result.provider_application_id,
            runtime_identity=result.runtime_identity,
            environment_fingerprint_hash=result.environment_fingerprint_hash,
            runtime_verified=runtime_verified,
            registry_reconciliation_status=registry_status,
            no_action_required=registry_status == "already_satisfied",
            conflict_diagnosis=diagnosis,
        )
        self._write_rollback_review(updated)
        deployment = self._replace(
            deployment,
            runtime_binding=(
                self._replace(
                    deployment.runtime_binding,
                    provider_job_id=result.provider_job_id,
                    provider_application_id=result.provider_application_id,
                    runtime_identity=result.runtime_identity,
                    environment_fingerprint_hash=result.environment_fingerprint_hash,
                )
                if deployment.runtime_binding
                else None
            ),
        )
        deployment = self._replace(deployment, status="rolled_back")
        deployment = self._replace(deployment, rolled_back_at=datetime.now(UTC))
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_rolled_back",
            actor=identity.actor_id,
            metric_version_id=review.to_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "rollback_review_id": review_id,
                "from_version": review.from_version,
                "to_version": review.to_version,
                "registry_reconciliation_status": registry_status,
            },
        )
        if registry_status == "already_satisfied":
            self._event(
                deployment.metric_definition_id,
                "convergence_already_satisfied",
                actor="production-service",
                metric_version_id=review.to_version_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "rollback_review_id": review_id,
                    "desired_version_id": review.to_version_id,
                    "actual_version_id": actual,
                    "conflict_category": None if diagnosis is None else diagnosis.category,
                    "execution_status": "skipped",
                },
            )
        else:
            self._event(
                deployment.metric_definition_id,
                "deployment_reconciled",
                actor="production-service",
                metric_version_id=review.to_version_id,
                metadata={"production_deployment_id": deployment.deployment_id},
            )
        # Phase 9: an approved rollback establishes the desired state. A later
        # convergence check therefore knows that v1 is the target, not merely that
        # the registry and the runtime happen to agree with each other.
        self._establish_desired_state(
            metric_definition_id=review.metric_definition_id,
            metric_key=review.metric_key,
            environment_id=deployment.environment_id,
            desired_version=self.registry.version_by_id(review.to_version_id),
            source="rollback",
            source_action_id=review_id,
            deployment_id=deployment.deployment_id,
            identity=identity,
            detail="established by the approved production rollback review",
        )
        self.session.commit()
        logger.info(
            "production_rolled_back",
            extra={
                "actor_id": identity.actor_id,
                "metric_version_id": review.to_version_id,
                "production_deployment_id": deployment.deployment_id,
                "rollback_review_id": review_id,
                "status": "rolled_back",
                "registry_reconciliation_status": registry_status,
                "request_id": self.request_id,
            },
        )
        return updated

    def _named_rollback_reconciliation_failure(
        self, *, review, review_id, identity, runtime_verified, result, diagnosis, exc
    ):
        """The provider stopped the version but the registry did not follow.

        Phase 9 keeps the named partial failure and adds *why*: an external actor
        moved the pointer, or the runtime is unreadable. A human still decides.
        """
        updated = self.rollback_review(review_id)
        updated = self._replace(
            updated,
            registry_reconciliation_status="required",
            production_rollback_status="succeeded",
            runtime_verified=runtime_verified,
            provider_job_id=result.provider_job_id,
            provider_application_id=result.provider_application_id,
            conflict_diagnosis=diagnosis,
            no_action_required=False,
        )
        self._write_rollback_review(updated)
        row = self.session.get(ProductionDeploymentRow, review.deployment_id)
        current = decode(ProductionDeployment, row.deployment_json)
        current = self._replace(current, status="rollback_reconciliation_required")
        self._write(current)
        self._event(
            current.metric_definition_id,
            "convergence_conflict_diagnosed",
            actor="production-service",
            metric_version_id=current.metric_version_id,
            metadata={
                "production_deployment_id": current.deployment_id,
                "rollback_review_id": review_id,
                "category": diagnosis.category,
                "expected": diagnosis.expected,
                "actual": diagnosis.actual,
                "desired": diagnosis.desired,
                "safe_to_retry": diagnosis.safe_to_retry,
                "requires_manual_review": diagnosis.requires_manual_review,
            },
        )
        self._event(
            current.metric_definition_id,
            "deployment_reconciliation_required",
            actor="production-service",
            metric_version_id=current.metric_version_id,
            metadata={
                "production_deployment_id": current.deployment_id,
                "rollback_review_id": review_id,
                "category": diagnosis.category,
                "reason": str(exc),
            },
        )
        self.session.commit()
        logger.error(
            "production_rollback_reconciliation_required",
            extra={
                "actor_id": identity.actor_id,
                "production_deployment_id": current.deployment_id,
                "rollback_review_id": review_id,
                "status": "rollback_reconciliation_required",
                "conflict_category": diagnosis.category,
                "error_type": type(exc).__name__,
                "request_id": self.request_id,
            },
        )
        raise ProductionError("rollback_reconciliation_required") from exc

    def _write_rollback_review(self, review):
        row = self.session.get(ProductionRollbackReviewRow, review.rollback_review_id)
        row.decision = review.decision
        row.reviewer = review.reviewer
        row.reviewer_actor_id = review.reviewer_actor_id
        row.reviewer_roles = list(review.reviewer_roles)
        row.reviewer_auth_source = review.reviewer_auth_source
        row.reviewer_issuer = review.reviewer_issuer
        row.production_rollback_status = review.production_rollback_status
        row.registry_reconciliation_status = review.registry_reconciliation_status
        row.runtime_verified = review.runtime_verified
        row.provider_job_id = review.provider_job_id
        row.provider_application_id = review.provider_application_id
        row.preflight_json = (
            None if review.preflight is None else review.preflight.model_dump(mode="json")
        )
        row.review_json = review.model_dump(mode="json")
        row.review_hash = canonical_hash(review)
        row.reviewed_at = (
            review.reviewed_at.astimezone(UTC).replace(tzinfo=None) if review.reviewed_at else None
        )
        self.session.flush()

    # ------------------------------------------------------------ reconcile

    def _observe(self, deployment) -> DeploymentReconciliation:
        """Read the registry pointer and the runtime now. Pure: it writes nothing.

        Phase 9 needs the same observation from two surfaces - the reconciler that
        records a mismatch, and the read-only convergence view. Keeping the reading
        here means the three-way view and the recorded mismatch can never disagree
        about what the runtime said.
        """
        definition = self.registry.definition_by_id(deployment.metric_definition_id)
        status = self.adapter.status(deployment.deployment_id)
        registry_active = (
            self.registry.version_by_id(definition.active_version_id)
            if definition.active_version_id
            else None
        )
        if status.runtime_state == "unknown":
            verdict = "unknown"
        elif registry_active is not None and (
            registry_active.metric_version_id == status.active_version_id
        ):
            verdict = "consistent"
        elif registry_active is None and status.active_version_id is None:
            verdict = "consistent"
        else:
            verdict = "mismatch"
        findings = []
        if verdict == "mismatch":
            findings.append(
                {
                    "type": "deployment_state_mismatch",
                    "registry_active_version_id": definition.active_version_id,
                    "production_active_version_id": status.active_version_id,
                    "runtime_state": status.runtime_state,
                    "environment_fingerprint_hash": status.environment_fingerprint_hash,
                    "runtime_identity": status.runtime_identity,
                    "provider_application_id": status.provider_application_id,
                }
            )
        return DeploymentReconciliation(
            deployment_id=deployment.deployment_id,
            metric_definition_id=deployment.metric_definition_id,
            metric_version_id=deployment.metric_version_id,
            registry_active_version_id=definition.active_version_id,
            registry_active_version=registry_active.version if registry_active else None,
            production_runtime_state=status.runtime_state,
            production_active_version_id=status.active_version_id,
            # A runtime may be running something the registry never produced. That
            # is a fact to record, not an error - the version simply stays NULL.
            production_active_version=(
                version.version
                if (version := self.lookup_version(status.active_version_id)) is not None
                else None
            ),
            environment_fingerprint_hash=status.environment_fingerprint_hash,
            runtime_identity=status.runtime_identity,
            provider_application_id=status.provider_application_id,
            status=verdict,
            findings=findings,
        )

    def reconcile(self, deployment_id, identity: ActorIdentity):
        require_authenticated(identity)
        deployment = self.deployment(deployment_id)
        reconciliation = self._observe(deployment)
        verdict = reconciliation.status
        deployment = self._replace(deployment, reconciliation=reconciliation)
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "deployment_reconciled",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "status": verdict,
                "registry_active_version_id": reconciliation.registry_active_version_id,
                "production_active_version_id": reconciliation.production_active_version_id,
            },
        )
        if verdict == "mismatch":
            self._event(
                deployment.metric_definition_id,
                "deployment_reconciliation_required",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "registry_active_version_id": reconciliation.registry_active_version_id,
                    "production_active_version_id": reconciliation.production_active_version_id,
                },
            )
            AlertService(self.session, self.state, self.request_id).record(
                deployment=deployment,
                type="deployment_state_mismatch",
                severity="critical",
                evidence=reconciliation.findings[0],
                recommended="investigate",
                actor="production-service",
            )
        self.session.commit()
        return reconciliation

    # ------------------------------------------------------- verification

    def verification(self, deployment_id, identity: ActorIdentity):
        """One verdict per production section, then a policy-gated aggregate.

        The report never changes a deployment. It exists so an operator can see
        *which* part of the production path is still unverified instead of
        reading a single optimistic boolean.
        """
        require_authenticated(identity)
        deployment = self.deployment(deployment_id)
        environment = EnvironmentService(self.session, self.state, self.request_id)
        profile = environment.profile(deployment.environment_id)
        fingerprint = environment.latest_fingerprint(deployment.environment_id)
        rollback_review = (
            self.rollback_review(deployment.rollback_review_id)
            if deployment.rollback_review_id
            else None
        )
        report = build_verification_report(
            deployment=deployment,
            profile=profile,
            fingerprint=fingerprint,
            evidence=self.latest_evidence(deployment_id),
            telemetry_source=self._bound_telemetry_source(deployment_id),
            rollback_review=rollback_review,
            adapter_name=self.adapter.name,
            identity_trusted=self._trust_boundary() != "none",
            decision_actors_trusted=self._decision_actors_trusted(
                self._deployment_decision_source(deployment),
                None if rollback_review is None else rollback_review.reviewer_auth_source,
            ),
            # The admission policy is versioned and constructed here; a caller can
            # never supply or relax it.
            policy=ProductionVerificationPolicy(),
        )
        self.session.add(
            ProductionVerificationReportRow(
                production_verification_run_id=report.production_verification_run_id,
                deployment_id=report.deployment_id,
                metric_definition_id=report.metric_definition_id,
                metric_version_id=report.metric_version_id,
                overall=report.overall,
                production_runtime_verified=report.production_runtime_verified,
                production_deployed=report.production_deployed,
                report_json=report.model_dump(mode="json"),
                report_hash=report.report_hash,
                observed_at=report.observed_at.astimezone(UTC).replace(tzinfo=None),
            )
        )
        deployment = self._replace(
            deployment,
            verification_run_id=report.production_verification_run_id,
            real_environment_verified=report.production_runtime_verified,
        )
        self._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "production_verification_recorded",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "production_verification_run_id": report.production_verification_run_id,
                "overall": report.overall,
                "blocking": report.blocking,
                "not_verified": report.not_verified,
            },
        )
        self.session.commit()
        logger.info(
            "production_verification_recorded",
            extra={
                "actor_id": identity.actor_id,
                "production_deployment_id": deployment.deployment_id,
                "production_verification_run_id": report.production_verification_run_id,
                "status": report.overall,
                "request_id": self.request_id,
            },
        )
        return report

    def verification_reports(self, deployment_id):
        rows = self.session.scalars(
            select(ProductionVerificationReportRow)
            .where(ProductionVerificationReportRow.deployment_id == deployment_id)
            .order_by(ProductionVerificationReportRow.observed_at)
        )
        return [self.verification_report(row.production_verification_run_id) for row in rows]

    def verification_report(self, production_verification_run_id):
        row = self.session.get(ProductionVerificationReportRow, production_verification_run_id)
        if row is None:
            raise ProductionNotFound("production_verification_report_not_found")
        report = decode(ProductionVerificationReport, row.report_json)
        if row.report_hash != report.report_hash or verification_report_hash(report) != (
            report.report_hash
        ):
            raise ProductionError("production_verification_report_changed")
        return report

    def lookup_version(self, metric_version_id):
        """Resolve a runtime-reported version id, tolerating an unknown one."""
        if not metric_version_id:
            return None
        try:
            return self.registry.version_by_id(metric_version_id)
        except (RegistryError, AIRIError):
            return None

    def _deployment_decision_source(self, deployment):
        if deployment.review_id is None:
            return None
        row = self.session.get(ProductionDeploymentReviewRow, deployment.review_id)
        return None if row is None else row.reviewer_auth_source

    def _bound_telemetry_source(self, deployment_id):
        row = self.session.scalar(
            select(MonitoringSnapshotRow)
            .where(
                MonitoringSnapshotRow.deployment_id == deployment_id,
                MonitoringSnapshotRow.telemetry_source_id.is_not(None),
            )
            .order_by(MonitoringSnapshotRow.observation_time.desc())
        )
        if row is None:
            return None
        source_row = self.session.get(TrustedTelemetrySourceRow, row.telemetry_source_id)
        if source_row is None:
            return None
        return decode(TrustedTelemetrySource, source_row.source_json)

    # ------------------------------------------------ Phase 9: three-way view

    def desired_state(self, deployment_id):
        """The latest established target for this deployment's scope, if any."""
        deployment = self.deployment(deployment_id)
        return self._desired_state(deployment.metric_definition_id, deployment.environment_id)

    def convergence(self, deployment_id, identity: ActorIdentity) -> ProductionConvergence:
        """§20: desired vs registry vs runtime. Read-only; it repairs nothing.

        The runtime is re-read rather than taken from the stored reconciliation. A
        view built from a cached snapshot answers "what did the runtime say last
        time", which is the question that hid the Phase 8 rollback bug.
        """
        require_authenticated(identity)
        deployment = self.deployment(deployment_id)
        reconciliation = self._observe(deployment)
        desired = self._desired_state(deployment.metric_definition_id, deployment.environment_id)
        target = None if desired is None else desired.desired_metric_version_id
        runtime_state = observed_runtime_state(reconciliation)
        state, _ = three_way_state(
            runtime_state=runtime_state,
            desired_version_id=target,
            registry_version_id=reconciliation.registry_active_version_id,
            runtime_version_id=reconciliation.production_active_version_id,
        )
        conflict = None
        if state == "mismatch":
            # A mismatch is explained rather than re-raised: "already satisfied"
            # and "an external actor moved it" need opposite human responses.
            conflict = diagnose_conflict(
                expected=reconciliation.registry_active_version_id,
                actual=reconciliation.production_active_version_id,
                desired=target,
                runtime_state=runtime_state,
            )
        return build_convergence(
            deployment=deployment,
            reconciliation=reconciliation,
            desired=desired,
            version_lookup=self.lookup_version,
            adapter_authoritative=bool(self.adapter.authoritative),
            adapter_supports_recovery=bool(self.adapter.supports_rollback),
            conflict=conflict,
        )

    def health(self, deployment_id) -> ProductionHealth:
        """§53: a read-only aggregate over the three separate states.

        It authorises nothing. A workflow can be ``deployed`` while the runtime is
        ``mismatch``, and collapsing the two into one field is what made the Phase 8
        mismatch hard to see.
        """
        deployment = self.deployment(deployment_id)
        reconciliation = self._observe(deployment)
        desired = self._desired_state(deployment.metric_definition_id, deployment.environment_id)
        runtime_state = observed_runtime_state(reconciliation)
        state, _ = three_way_state(
            runtime_state=runtime_state,
            desired_version_id=None if desired is None else desired.desired_metric_version_id,
            registry_version_id=reconciliation.registry_active_version_id,
            runtime_version_id=reconciliation.production_active_version_id,
        )
        status, reasons = health_of(
            workflow_status=deployment.status,
            runtime_state=runtime_state,
            convergence=state,
        )
        return ProductionHealth(
            deployment_id=deployment.deployment_id,
            metric_definition_id=deployment.metric_definition_id,
            environment_id=deployment.environment_id,
            workflow_status=deployment.status,
            runtime_state=runtime_state,
            convergence_state=state,
            health=status,
            reasons=reasons,
        )

    def verification_gate(
        self, deployment_id, identity: ActorIdentity, action="deploy"
    ) -> VerificationGateDecision:
        """§47: what the gate decides, without performing the action.

        ``persist=False`` on purpose. The decision that actually gates something is
        recorded by the action itself, so that a gate decision in the audit trail
        always corresponds to an action that was really attempted.
        """
        require_authenticated(identity)
        deployment = self.deployment(deployment_id)
        return self._evaluate_gate(
            action=action, deployment=deployment, identity=identity, persist=False
        )

    def gate_decisions(self, deployment_id, action=None):
        statement = (
            select(VerificationGateDecisionRow)
            .where(VerificationGateDecisionRow.deployment_id == deployment_id)
            .order_by(VerificationGateDecisionRow.decided_at)
        )
        if action:
            statement = statement.where(VerificationGateDecisionRow.action == action)
        return [
            decode(VerificationGateDecision, row.decision_json)
            for row in self.session.scalars(statement)
        ]

    # ----------------------------------------------------- reconciliation

    def plan_reconciliation(self, deployment_id, identity: ActorIdentity):
        """§39: plans a recovery; it never declares which side is the truth."""
        authorize(identity, "plan_production_reconciliation")
        self._require_trusted_when_configured(identity)
        reconciliation = self.reconcile(deployment_id, identity)
        deployment = self.deployment(deployment_id)
        # §11: the third state. Without it the plan can only say "these two
        # disagree", which is what made an already-satisfied target look broken.
        desired = self._desired_state(deployment.metric_definition_id, deployment.environment_id)
        target = None if desired is None else desired.desired_metric_version_id
        # A pointer and a runtime that agree can still both sit away from the
        # desired state - after an unexecuted rollback, say. Refusing to plan there
        # would report "nothing to do" about a real open commitment.
        if reconciliation.status == "consistent" and (
            target is None or target == reconciliation.production_active_version_id
        ):
            raise ProductionError("production_reconciliation_not_required")
        evidence = self.latest_evidence(deployment_id)
        alert_ids = [
            alert.alert_id
            for alert in AlertService(self.session, self.state, self.request_id).alerts(
                deployment_id
            )
            if alert.status in ("open", "acknowledged")
        ]
        plan = build_reconciliation_plan(
            deployment=deployment,
            reconciliation=reconciliation,
            evidence=evidence,
            latest_deployment_review_id=deployment.review_id,
            rollback_review_ids=(
                [deployment.rollback_review_id] if deployment.rollback_review_id else []
            ),
            alert_ids=alert_ids,
            # A version the registry never produced must resolve to None, not raise.
            version_lookup=self.lookup_version,
            adapter_name=self.adapter.name,
            adapter_authoritative=bool(self.adapter.authoritative),
            adapter_supports_recovery=bool(self.adapter.supports_rollback),
            created_by=identity.actor_id,
            desired=desired,
        )
        self.session.add(
            ReconciliationPlanRow(
                reconciliation_plan_id=plan.reconciliation_plan_id,
                deployment_id=plan.deployment_id,
                metric_definition_id=plan.metric_definition_id,
                metric_version_id=plan.metric_version_id,
                verdict=plan.verdict,
                recommended_action=plan.recommended_action,
                plan_json=plan.model_dump(mode="json"),
                plan_hash=canonical_hash(plan),
                created_by=plan.created_by,
                created_at=utcnow(),
            )
        )
        self._event(
            deployment.metric_definition_id,
            "reconciliation_planned",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "reconciliation_plan_id": plan.reconciliation_plan_id,
                "verdict": plan.verdict,
                "possible_actions": list(plan.possible_actions),
                "recommended_action": plan.recommended_action,
            },
        )
        self.session.commit()
        logger.info(
            "reconciliation_planned",
            extra={
                "actor_id": identity.actor_id,
                "production_deployment_id": deployment.deployment_id,
                "reconciliation_plan_id": plan.reconciliation_plan_id,
                "recommended_action": plan.recommended_action,
                "request_id": self.request_id,
            },
        )
        return plan

    def reconciliation_plan(self, reconciliation_plan_id):
        row = self.session.get(ReconciliationPlanRow, reconciliation_plan_id)
        if row is None:
            raise ProductionNotFound("reconciliation_plan_not_found")
        plan = decode(ReconciliationPlan, row.plan_json)
        if row.plan_hash != canonical_hash(plan):
            raise ProductionError("reconciliation_plan_changed")
        return plan

    def reconciliation_plans(self, deployment_id):
        rows = self.session.scalars(
            select(ReconciliationPlanRow)
            .where(ReconciliationPlanRow.deployment_id == deployment_id)
            .order_by(ReconciliationPlanRow.created_at)
        )
        return [self.reconciliation_plan(row.reconciliation_plan_id) for row in rows]

    def create_reconciliation_review(
        self, reconciliation_plan_id, payload: ReconciliationReviewCreate, identity: ActorIdentity
    ):
        """Opens the human decision surface. It executes nothing on its own."""
        require_authenticated(identity)
        plan = self.reconciliation_plan(reconciliation_plan_id)
        if self._reconciliation_review_for_plan(reconciliation_plan_id) is not None:
            raise ProductionError("reconciliation_review_already_exists")
        review = ReconciliationReview(
            reconciliation_plan_id=plan.reconciliation_plan_id,
            deployment_id=plan.deployment_id,
            comment=payload.comment,
        )
        self.session.add(
            ReconciliationReviewRow(
                reconciliation_review_id=review.reconciliation_review_id,
                reconciliation_plan_id=review.reconciliation_plan_id,
                deployment_id=review.deployment_id,
                decision="pending",
                comment=review.comment,
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
                reviewed_at=None,
            )
        )
        self.session.commit()
        return review

    def reconciliation_review(self, reconciliation_review_id):
        row = self.session.get(ReconciliationReviewRow, reconciliation_review_id)
        if row is None:
            raise ProductionNotFound("reconciliation_review_not_found")
        review = decode(ReconciliationReview, row.review_json)
        if row.review_hash != canonical_hash(review):
            raise ProductionError("reconciliation_review_changed")
        return review

    def _reconciliation_review_for_plan(self, reconciliation_plan_id):
        row = self.session.scalar(
            select(ReconciliationReviewRow).where(
                ReconciliationReviewRow.reconciliation_plan_id == reconciliation_plan_id
            )
        )
        return None if row is None else decode(ReconciliationReview, row.review_json)

    def decide_reconciliation_review(
        self,
        reconciliation_review_id,
        payload: ReconciliationReviewDecision,
        identity: ActorIdentity,
    ):
        """Executes the chosen recovery, then re-observes to confirm convergence."""
        authorize(identity, payload.decision)
        self._require_trusted_when_configured(identity)
        review = self.reconciliation_review(reconciliation_review_id)
        if review.decision != "pending":
            raise ProductionError("reconciliation_review_already_decided")
        plan = self.reconciliation_plan(review.reconciliation_plan_id)
        action = DECISION_TO_ACTION[payload.decision]
        if action not in plan.possible_actions:
            raise ProductionError("reconciliation_action_not_available")
        deployment = self.deployment(plan.deployment_id)
        updated = self._replace(
            review,
            decision=payload.decision,
            reviewer=identity.display_name,
            reviewer_actor_id=identity.actor_id,
            reviewer_roles=sorted(identity.roles),
            reviewer_auth_source=identity.auth_source,
            reviewer_issuer=identity.issuer,
            comment=payload.comment,
            execution_required=payload.decision != "keep_mismatch_for_investigation",
            reviewed_at=datetime.now(UTC),
        )
        self.session.execute(
            update(ReconciliationReviewRow)
            .where(
                ReconciliationReviewRow.reconciliation_review_id == reconciliation_review_id,
                ReconciliationReviewRow.decision == "pending",
            )
            .values(
                decision=payload.decision,
                reviewer=identity.display_name,
                reviewer_actor_id=identity.actor_id,
                reviewer_roles=sorted(identity.roles),
                reviewer_auth_source=identity.auth_source,
                reviewer_issuer=identity.issuer,
                comment=payload.comment,
                review_json=updated.model_dump(mode="json"),
                review_hash=canonical_hash(updated),
                reviewed_at=utcnow(),
            )
        )
        self._event(
            deployment.metric_definition_id,
            "reconciliation_approved",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "reconciliation_plan_id": plan.reconciliation_plan_id,
                "reconciliation_review_id": reconciliation_review_id,
                "decision": payload.decision,
                "action": action,
            },
        )
        self.session.commit()

        return self._converge(
            plan,
            action=action,
            decision=payload.decision,
            identity=identity,
            review_id=reconciliation_review_id,
        )

    def execute_reconciliation_plan(self, reconciliation_plan_id, identity: ActorIdentity):
        """§16: run an already-approved convergence, idempotently.

        The human decision must already exist; this method never chooses an action
        and never approves one. Running it twice is safe and is the point: the
        second call observes the target, finds it satisfied, and reports
        ``target_state_already_satisfied`` instead of forcing a compare-and-set
        that has nothing left to change.
        """
        authorize(identity, "execute_production_reconciliation")
        self._require_trusted_when_configured(identity)
        plan = self.reconciliation_plan(reconciliation_plan_id)
        review = self._reconciliation_review_for_plan(reconciliation_plan_id)
        if review is None:
            raise ProductionError("reconciliation_review_required")
        if review.decision == "pending":
            raise ProductionError("reconciliation_review_pending")
        return self._converge(
            plan,
            action=DECISION_TO_ACTION[review.decision],
            decision=review.decision,
            identity=identity,
            review_id=review.reconciliation_review_id,
        )

    def _converge(
        self,
        plan: ReconciliationPlan,
        *,
        action,
        decision,
        identity: ActorIdentity,
        review_id,
    ) -> ReconciliationResult:
        """Observe, then act only if acting is still needed.

        The observation comes first on purpose. A compare-and-set can fail *because*
        the goal was already met, and treating that as a conflict is exactly the
        Phase 8 rollback bug: a human reconciliation had already moved the pointer to
        the rollback target, and the rollback then reported a failure that was really
        a success.
        """
        self._require_trusted_when_configured(identity)
        deployment = self.deployment(plan.deployment_id)
        desired = self._desired_state(deployment.metric_definition_id, deployment.environment_id)
        desired_state_id = None if desired is None else desired.desired_state_id
        pre_execution = self.reconcile(deployment.deployment_id, identity)
        runtime_state = observed_runtime_state(pre_execution)
        target = None if desired is None else desired.desired_metric_version_id
        if action == "runtime_to_registry":
            # Choosing the runtime as the fact *is* a governance decision. It
            # re-establishes the desired state as whatever the runtime reports, so
            # "converged" afterwards means the registry, the runtime and the new
            # target agree - not that the decision quietly failed.
            target = pre_execution.production_active_version_id
        state, satisfied = three_way_state(
            runtime_state=runtime_state,
            desired_version_id=target,
            registry_version_id=pre_execution.registry_active_version_id,
            runtime_version_id=pre_execution.production_active_version_id,
        )

        if satisfied:
            # §16: nothing needed doing, and that is a governance result to record.
            result = ReconciliationResult(
                reconciliation_plan_id=plan.reconciliation_plan_id,
                reconciliation_review_id=review_id,
                deployment_id=deployment.deployment_id,
                action=decision,
                execution_status="skipped",
                convergence_status="verified",
                final_status="already_converged",
                pre_execution=pre_execution,
                post_execution=pre_execution,
                detail=(
                    "the desired state is already satisfied by the registry and the "
                    "runtime, so no recovery was executed"
                ),
                executed_by=identity.actor_id,
                executed_at=datetime.now(UTC),
                convergence_action="no_op",
                convergence_verdict=convergence_verdict(state=state, executed=False),
                target_already_satisfied=True,
                skipped_reason="target_state_already_satisfied",
                desired_state_id=desired_state_id,
            )
            self._event(
                deployment.metric_definition_id,
                "convergence_already_satisfied",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "reconciliation_plan_id": plan.reconciliation_plan_id,
                    "convergence_state": state,
                    "skipped_reason": result.skipped_reason,
                },
            )
            self.session.commit()
            return self._persist_reconciliation_result(result)

        if action == "manual_investigation":
            result = ReconciliationResult(
                reconciliation_plan_id=plan.reconciliation_plan_id,
                reconciliation_review_id=review_id,
                deployment_id=deployment.deployment_id,
                action=decision,
                execution_status="skipped",
                convergence_status="not_verified",
                final_status="reconciliation_not_executed",
                pre_execution=pre_execution,
                detail="a human chose to keep the mismatch for investigation",
                executed_by=identity.actor_id,
                executed_at=datetime.now(UTC),
                convergence_action="manual_investigation",
                convergence_verdict=convergence_verdict(state=state, executed=False),
                desired_state_id=desired_state_id,
            )
            self.session.commit()
            return self._persist_reconciliation_result(result)

        if action == "registry_to_runtime":
            # §47: the gate runs *before* the recovery touches the runtime, exactly
            # as it does before a deploy. A reconciliation is still a production
            # action, so it is gated like one.
            gate_decision = self._evaluate_gate(
                action="reconciliation", deployment=deployment, identity=identity
            )
            if gate_decision.result == "blocked":
                raise ProductionError("production_verification_gate_blocked")

        execution_status, failure_detail, provider_job_id = self._execute_reconciliation(
            plan, decision, identity
        )
        if execution_status == "failed":
            result = ReconciliationResult(
                reconciliation_plan_id=plan.reconciliation_plan_id,
                reconciliation_review_id=review_id,
                deployment_id=deployment.deployment_id,
                action=decision,
                execution_status=execution_status,
                convergence_status="not_verified",
                final_status="reconciliation_failed",
                pre_execution=pre_execution,
                provider_job_id=provider_job_id,
                detail=failure_detail,
                executed_by=identity.actor_id,
                executed_at=datetime.now(UTC),
                convergence_action=action,
                convergence_verdict="not_verified",
                desired_state_id=desired_state_id,
            )
            self._event(
                deployment.metric_definition_id,
                "reconciliation_executed",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "reconciliation_plan_id": plan.reconciliation_plan_id,
                    "execution_status": execution_status,
                    "final_status": result.final_status,
                },
            )
            self.session.commit()
            return self._persist_reconciliation_result(result)

        # The recovery reported success. That is not proof; only re-observation is.
        post_execution = self.reconcile(deployment.deployment_id, identity)
        after_state, _ = three_way_state(
            runtime_state=observed_runtime_state(post_execution),
            desired_version_id=target,
            registry_version_id=post_execution.registry_active_version_id,
            runtime_version_id=post_execution.production_active_version_id,
        )
        executed = execution_status == "executed"
        convergence = convergence_status(post_execution)
        verdict = convergence_verdict(state=after_state, executed=executed)
        verified = convergence == "verified"
        if verdict == "already_converged":
            final_status = "already_converged"
        elif verified:
            final_status = "reconciliation_completed"
        else:
            final_status = "reconciliation_verification_required"
        result = ReconciliationResult(
            reconciliation_plan_id=plan.reconciliation_plan_id,
            reconciliation_review_id=review_id,
            deployment_id=deployment.deployment_id,
            action=decision,
            execution_status=execution_status,
            convergence_status=convergence,
            final_status=final_status,
            pre_execution=pre_execution,
            post_execution=post_execution,
            provider_job_id=provider_job_id,
            detail=(
                "the runtime was re-observed and now agrees with the registry"
                if verified
                else "the recovery ran but the runtime still does not agree with the registry"
            ),
            executed_by=identity.actor_id,
            executed_at=datetime.now(UTC),
            convergence_action=action,
            convergence_verdict=verdict,
            desired_state_id=desired_state_id,
        )
        self._event(
            deployment.metric_definition_id,
            "reconciliation_executed",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "reconciliation_plan_id": plan.reconciliation_plan_id,
                "action": decision,
                "execution_status": execution_status,
                "convergence_status": convergence,
                "convergence_verdict": verdict,
            },
        )
        if verdict == "already_converged":
            self._event(
                deployment.metric_definition_id,
                "convergence_already_satisfied",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "reconciliation_plan_id": plan.reconciliation_plan_id,
                    "convergence_state": after_state,
                    "skipped_reason": "target_state_already_satisfied",
                },
            )
        if verified:
            self._event(
                deployment.metric_definition_id,
                "reconciliation_verified",
                actor="production-service",
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "reconciliation_plan_id": plan.reconciliation_plan_id,
                    "reconciliation_result_id": result.reconciliation_result_id,
                },
            )
            # The converged version is now the target: record it as such, derived
            # from the runtime rather than from anything a caller supplied (§12).
            converged = self.lookup_version(post_execution.production_active_version_id)
            if converged is not None:
                self._establish_desired_state(
                    metric_definition_id=deployment.metric_definition_id,
                    metric_key=deployment.metric_key,
                    environment_id=deployment.environment_id,
                    desired_version=converged,
                    source="reconciliation",
                    source_action_id=plan.reconciliation_plan_id,
                    identity=identity,
                    deployment_id=deployment.deployment_id,
                    detail="a reconciliation converged the registry and the runtime",
                )
        self.session.commit()
        return self._persist_reconciliation_result(result)

    def _execute_reconciliation(self, plan, decision, identity):
        """Performs exactly one governed recovery. Returns (status, detail, job_id)."""
        if decision == "align_runtime_to_registry":
            deployment = self.deployment(plan.deployment_id)
            result = self.adapter.deploy(deployment.package, deployment.deployment_id)
            if result.status != "deployed":
                return "failed", result.detail or "provider did not confirm the recovery", None
            return "executed", None, result.provider_job_id
        if decision == "align_registry_to_runtime":
            target_version_id = plan.production_active_version_id
            definition = self.registry.definition_by_id(plan.metric_definition_id)
            if target_version_id is None or definition.active_version_id == target_version_id:
                return "skipped", "the registry already points at the runtime version", None
            try:
                self.registry.reconcile_active_version(
                    plan.metric_definition_id,
                    from_version_id=definition.active_version_id,
                    to_version_id=target_version_id,
                    actor=identity.actor_id,
                    reason="production reconciliation aligned the registry to the runtime",
                )
            except AIRIError as exc:
                return "failed", f"registry pointer could not be moved: {exc.code}", None
            return "executed", None, None
        return "skipped", "no executable recovery was selected", None

    def _persist_reconciliation_result(self, result: ReconciliationResult):
        self.session.add(
            ReconciliationResultRow(
                reconciliation_result_id=result.reconciliation_result_id,
                reconciliation_plan_id=result.reconciliation_plan_id,
                reconciliation_review_id=result.reconciliation_review_id,
                deployment_id=result.deployment_id,
                action=result.action,
                execution_status=result.execution_status,
                convergence_status=result.convergence_status,
                final_status=result.final_status,
                result_json=result.model_dump(mode="json"),
                result_hash=canonical_hash(result),
                created_at=utcnow(),
            )
        )
        self.session.commit()
        logger.info(
            "reconciliation_result_recorded",
            extra={
                "production_deployment_id": result.deployment_id,
                "reconciliation_result_id": result.reconciliation_result_id,
                "status": result.final_status,
                "request_id": self.request_id,
            },
        )
        return result

    def reconciliation_result(self, reconciliation_result_id):
        row = self.session.get(ReconciliationResultRow, reconciliation_result_id)
        if row is None:
            raise ProductionNotFound("reconciliation_result_not_found")
        result = decode(ReconciliationResult, row.result_json)
        if row.result_hash != canonical_hash(result):
            raise ProductionError("reconciliation_result_changed")
        return result

    def reconciliation_results(self, deployment_id):
        rows = self.session.scalars(
            select(ReconciliationResultRow)
            .where(ReconciliationResultRow.deployment_id == deployment_id)
            .order_by(ReconciliationResultRow.created_at)
        )
        return [self.reconciliation_result(row.reconciliation_result_id) for row in rows]


# -------------------------------------------------------------------- monitoring


class MonitoringService(_ProductionService):
    def ingest(self, payload: MonitoringSnapshotRequest, identity: ActorIdentity):
        require_authenticated(identity)
        service = DeploymentService(self.session, self.state, self.request_id, self.adapter)
        deployment = service.deployment(payload.deployment_id)
        if deployment.status not in DEPLOYABLE_STATUSES:
            raise ProductionError("production_deployment_not_monitoring")
        policy = deployment.monitoring_policy
        baseline = deployment.monitoring_baseline
        # Phase 8: telemetry is attributed. An unattributed producer is refused
        # outright; a registered-but-unverified producer is accepted but marked,
        # because "received" is not "trusted".
        source = self.telemetry_source(payload.telemetry_source_id)
        trust = self._telemetry_trust(source, deployment)
        received_at = datetime.now(UTC)
        freshness, lag_hours = telemetry_freshness(payload.observation_time, received_at, policy)
        duplicate = self._snapshot_by_source_event(
            source.telemetry_source_id, payload.source_event_id
        )
        if duplicate is not None:
            return duplicate, [], [], ["duplicate_telemetry_event_ignored"]
        psi, psi_status = runtime_psi(
            None if baseline is None else baseline.distribution,
            payload.distribution,
            policy.psi_epsilon,
        )
        window_start = (
            payload.observation_time.astimezone(UTC) - timedelta(hours=policy.window_hours)
        ).replace(tzinfo=None)
        rows = self.session.scalars(
            select(MonitoringSnapshotRow)
            .where(
                MonitoringSnapshotRow.deployment_id == deployment.deployment_id,
                MonitoringSnapshotRow.observation_time >= window_start,
            )
            .order_by(MonitoringSnapshotRow.observation_time)
        )
        previous = [decode(MonitoringSnapshot, row.snapshot_json) for row in rows]
        snapshot = MonitoringSnapshot(
            deployment_id=deployment.deployment_id,
            metric_definition_id=deployment.metric_definition_id,
            metric_version_id=deployment.metric_version_id,
            metric_key=deployment.metric_key,
            version=deployment.version,
            observation_time=payload.observation_time,
            execution=payload.execution,
            quality=payload.quality,
            distribution=payload.distribution,
            source_system=payload.source_system,
            source_event_id=payload.source_event_id,
            publisher_actor_id=identity.actor_id,
            telemetry_source_id=source.telemetry_source_id,
            telemetry_trust=trust,
            telemetry_freshness=freshness,
            received_at=received_at,
            observation_lag_hours=lag_hours,
            monitoring_policy_version=policy.version,
            monitoring_policy_hash=policy.policy_hash,
            baseline_hash=None if baseline is None else baseline.baseline_hash,
            psi=psi,
            psi_status=psi_status,
            content_hash="0" * 64,
        )
        expectation = self.expectation(deployment.deployment_id)
        findings, summary, warnings = MonitoringAssessor().assess(
            snapshot=snapshot,
            baseline=baseline,
            policy=policy,
            window_executions=tuple(item.execution.status for item in previous),
            previous_observation_time=previous[-1].observation_time if previous else None,
            expectation_overdue=bool(expectation) and freshness == "late",
        )
        if trust != "trusted_source":
            warnings.append("telemetry_source_not_trusted")
        if freshness != "fresh":
            warnings.append(f"telemetry_{freshness}")
        snapshot = self._replace(
            snapshot, findings=summary, content_hash=monitoring_snapshot_content_hash(snapshot)
        )
        self.session.add(
            MonitoringSnapshotRow(
                monitoring_snapshot_id=snapshot.monitoring_snapshot_id,
                deployment_id=snapshot.deployment_id,
                metric_version_id=snapshot.metric_version_id,
                observation_time=snapshot.observation_time.astimezone(UTC).replace(tzinfo=None),
                received_at=snapshot.received_at.astimezone(UTC).replace(tzinfo=None),
                execution_status=snapshot.execution.status,
                row_count=snapshot.execution.row_count,
                psi=snapshot.psi,
                source_system=snapshot.source_system,
                source_event_id=snapshot.source_event_id,
                telemetry_source_id=snapshot.telemetry_source_id,
                telemetry_trust=snapshot.telemetry_trust,
                telemetry_freshness=snapshot.telemetry_freshness,
                observation_lag_hours=snapshot.observation_lag_hours,
                monitoring_policy_version=snapshot.monitoring_policy_version,
                publisher_actor_id=snapshot.publisher_actor_id,
                snapshot_json=snapshot.model_dump(mode="json"),
                content_hash=snapshot.content_hash,
                created_at=utcnow(),
            )
        )
        if deployment.status == "deployed":
            deployment = service._transition(deployment, "monitoring")
            service._write(deployment)
        self._event(
            deployment.metric_definition_id,
            "monitoring_snapshot_ingested",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "monitoring_snapshot_id": snapshot.monitoring_snapshot_id,
                "telemetry_source_id": snapshot.telemetry_source_id,
                "telemetry_trust": snapshot.telemetry_trust,
                "telemetry_freshness": snapshot.telemetry_freshness,
                "observation_lag_hours": snapshot.observation_lag_hours,
                "monitoring_policy_version": snapshot.monitoring_policy_version,
                "psi": snapshot.psi,
                "psi_status": snapshot.psi_status,
                "execution_status": snapshot.execution.status,
            },
        )
        alerts = []
        created = []
        alerts_service = AlertService(self.session, self.state, self.request_id, self.adapter)
        for finding in findings:
            alert, is_new = alerts_service.record(
                deployment=deployment,
                type=finding.type,
                severity=finding.severity,
                evidence=finding.evidence,
                recommended=recommended_action(finding.severity),
                actor="monitoring-service",
                dedup_key=finding.dedup_key,
            )
            alerts.append(alert)
            if is_new:
                created.append(alert.alert_id)
        self.session.commit()
        logger.info(
            "monitoring_snapshot_ingested",
            extra={
                "actor_id": identity.actor_id,
                "metric_definition_id": deployment.metric_definition_id,
                "metric_version_id": deployment.metric_version_id,
                "production_deployment_id": deployment.deployment_id,
                "monitoring_snapshot_id": snapshot.monitoring_snapshot_id,
                "row_count": snapshot.execution.row_count,
                "status": snapshot.execution.status,
                "request_id": self.request_id,
            },
        )
        return snapshot, alerts, created, warnings

    def snapshots(self, deployment_id):
        rows = self.session.scalars(
            select(MonitoringSnapshotRow)
            .where(MonitoringSnapshotRow.deployment_id == deployment_id)
            .order_by(MonitoringSnapshotRow.observation_time)
        )
        return [decode(MonitoringSnapshot, row.snapshot_json) for row in rows]

    # ------------------------------------------------------ trusted telemetry

    def register_telemetry_source(
        self, payload: TrustedTelemetrySourceRequest, identity: ActorIdentity
    ):
        """Registers a producer. `verified` is never set from a caller."""
        authorize(identity, "register_trusted_telemetry_source")
        EnvironmentService(self.session, self.state, self.request_id).profile(
            payload.environment_id
        )
        if self.session.get(TrustedTelemetrySourceRow, payload.telemetry_source_id) is not None:
            raise ProductionError("telemetry_source_already_registered")
        source = TrustedTelemetrySource(
            telemetry_source_id=payload.telemetry_source_id,
            source_system=payload.source_system,
            environment_id=payload.environment_id,
            auth_identity=payload.auth_identity,
            schema_version=payload.schema_version,
            registered_by=identity.actor_id,
            verified=False,
        )
        self.session.add(
            TrustedTelemetrySourceRow(
                telemetry_source_id=source.telemetry_source_id,
                source_system=source.source_system,
                environment_id=source.environment_id,
                auth_identity=source.auth_identity,
                schema_version=source.schema_version,
                verified=source.verified,
                synthetic=source.synthetic,
                registered_by=source.registered_by,
                source_json=source.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        self._event(
            TELEMETRY_SCOPE,
            "telemetry_source_registered",
            actor=identity.actor_id,
            metadata={
                "telemetry_source_id": source.telemetry_source_id,
                "environment_id": source.environment_id,
                "source_system": source.source_system,
                "verified": source.verified,
            },
        )
        self.session.commit()
        logger.info(
            "telemetry_source_registered",
            extra={
                "actor_id": identity.actor_id,
                "telemetry_source_id": source.telemetry_source_id,
                "environment_id": source.environment_id,
                "status": "verified" if source.verified else "not_verified",
                "request_id": self.request_id,
            },
        )
        return source

    def verify_telemetry_source(
        self, telemetry_source_id, identity: ActorIdentity, *, note=None, synthetic=False
    ):
        """Operator attestation that the producer authenticates as declared.

        This is the only path that can flip `verified`. It is a human decision,
        recorded with a note, and it never inspects a live producer - AIRI cannot
        prove a remote producer's credential in-process, so it records who
        vouched for it instead of pretending it was machine-verified.
        """
        authorize(identity, "register_trusted_telemetry_source")
        source = self.telemetry_source(telemetry_source_id)
        if source.verified and not synthetic:
            raise ProductionError("telemetry_source_already_verified")
        updated = self._replace(source, verified=True, synthetic=synthetic, verification_note=note)
        self.session.execute(
            update(TrustedTelemetrySourceRow)
            .where(TrustedTelemetrySourceRow.telemetry_source_id == telemetry_source_id)
            .values(
                verified=True,
                synthetic=synthetic,
                source_json=updated.model_dump(mode="json"),
            )
        )
        self._event(
            TELEMETRY_SCOPE,
            "telemetry_source_verified",
            actor=identity.actor_id,
            metadata={
                "telemetry_source_id": telemetry_source_id,
                "synthetic": synthetic,
                "note": note,
            },
        )
        self.session.commit()
        logger.info(
            "telemetry_source_verified",
            extra={
                "actor_id": identity.actor_id,
                "telemetry_source_id": telemetry_source_id,
                "synthetic": synthetic,
                "request_id": self.request_id,
            },
        )
        return self.telemetry_source(telemetry_source_id)

    def telemetry_source(self, telemetry_source_id):
        row = self.session.get(TrustedTelemetrySourceRow, telemetry_source_id)
        if row is None:
            raise ProductionNotFound("telemetry_source_not_registered")
        source = decode(TrustedTelemetrySource, row.source_json)
        if bool(row.verified) != source.verified or row.environment_id != source.environment_id:
            raise ProductionError("telemetry_source_integrity_error")
        return source

    def telemetry_sources(self, environment_id=None):
        statement = select(TrustedTelemetrySourceRow).order_by(
            TrustedTelemetrySourceRow.telemetry_source_id
        )
        if environment_id is not None:
            statement = statement.where(TrustedTelemetrySourceRow.environment_id == environment_id)
        return [
            self.telemetry_source(row.telemetry_source_id)
            for row in self.session.scalars(statement)
        ]

    def _snapshot_by_source_event(self, telemetry_source_id, source_event_id):
        row = self.session.scalar(
            select(MonitoringSnapshotRow).where(
                MonitoringSnapshotRow.telemetry_source_id == telemetry_source_id,
                MonitoringSnapshotRow.source_event_id == source_event_id,
            )
        )
        return None if row is None else decode(MonitoringSnapshot, row.snapshot_json)

    @staticmethod
    def _telemetry_trust(source: TrustedTelemetrySource, deployment) -> str:
        if source.synthetic:
            return "synthetic"
        if source.verified and source.environment_id == deployment.environment_id:
            return "trusted_source"
        return "unverified"

    # ---------------------------------------------------------- expectations

    def register_expectation(self, payload: MonitoringExpectationRequest, identity: ActorIdentity):
        """Declares when telemetry should arrive. It schedules nothing."""
        require_authenticated(identity)
        service = DeploymentService(self.session, self.state, self.request_id, self.adapter)
        deployment = service.deployment(payload.deployment_id)
        if deployment.status not in DEPLOYABLE_STATUSES:
            raise ProductionError("production_deployment_not_monitoring")
        if self.expectation(payload.deployment_id) is not None:
            raise ProductionError("monitoring_expectation_already_registered")
        if payload.telemetry_source_id is not None:
            source = self.telemetry_source(payload.telemetry_source_id)
            if source.environment_id != deployment.environment_id:
                raise ProductionError("telemetry_source_environment_mismatch")
        expectation = MonitoringExpectation(
            deployment_id=deployment.deployment_id,
            metric_definition_id=deployment.metric_definition_id,
            metric_version_id=deployment.metric_version_id,
            environment_id=deployment.environment_id,
            telemetry_source_id=payload.telemetry_source_id,
            expected_interval_hours=payload.expected_interval_hours,
            grace_hours=payload.grace_hours,
            created_by=identity.actor_id,
        )
        self.session.add(
            MonitoringExpectationRow(
                monitoring_expectation_id=expectation.monitoring_expectation_id,
                deployment_id=expectation.deployment_id,
                metric_definition_id=expectation.metric_definition_id,
                metric_version_id=expectation.metric_version_id,
                environment_id=expectation.environment_id,
                telemetry_source_id=expectation.telemetry_source_id,
                expected_interval_hours=expectation.expected_interval_hours,
                grace_hours=expectation.grace_hours,
                created_by=expectation.created_by,
                expectation_json=expectation.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        self._event(
            deployment.metric_definition_id,
            "monitoring_expectation_registered",
            actor=identity.actor_id,
            metric_version_id=deployment.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": deployment.deployment_id,
                "monitoring_expectation_id": expectation.monitoring_expectation_id,
                "expected_interval_hours": expectation.expected_interval_hours,
                "grace_hours": expectation.grace_hours,
            },
        )
        self.session.commit()
        return expectation

    def expectation(self, deployment_id):
        row = self.session.scalar(
            select(MonitoringExpectationRow).where(
                MonitoringExpectationRow.deployment_id == deployment_id
            )
        )
        return None if row is None else decode(MonitoringExpectation, row.expectation_json)

    def expectation_status(self, deployment_id) -> MonitoringExpectationStatus:
        """Compare the last observation against the declaration. Report only.

        §24: evaluating is not alerting. "No data arrived" is a fact about a
        pipeline, and turning it into a production alarm automatically would blame
        the metric for a transport failure. The operational run
        (:meth:`evaluate_expectations`) is where an alert gets decided, explicitly
        and with its own record.
        """
        DeploymentService(self.session, self.state, self.request_id, self.adapter).deployment(
            deployment_id
        )
        expectation = self.expectation(deployment_id)
        if expectation is None:
            raise ProductionNotFound("monitoring_expectation_not_registered")
        snapshots = self.snapshots(deployment_id)
        return evaluate_expectation(
            expectation=expectation,
            last_snapshot=snapshots[-1] if snapshots else None,
            now=datetime.now(UTC),
            snapshot_count=len(snapshots),
        )

    # Phase 8 name, kept so the existing read-only route keeps its meaning.
    def evaluate_expectation(self, deployment_id) -> MonitoringExpectationStatus:
        return self.expectation_status(deployment_id)

    def evaluate_expectations(
        self, identity: ActorIdentity, *, deployment_id: str | None = None
    ) -> ExpectationEvaluationRun:
        """§21/§23: the operation an external scheduler actually calls.

        AIRI owns the question ("has the declared window closed?") and not the
        ticking, so a cron job, an Argo workflow or a Databricks job calls this
        endpoint. Nothing here starts a timer.

        Two things happen, in this order and deliberately separable:

        1. every expectation is evaluated and the run is recorded, so "nobody
           checked" and "we checked and it was fine" cannot look alike; and
        2. a finding raises an *alert* (deduplicated per missed window, so
           re-evaluating the same window is idempotent), and the alert is offered
           to the configured notification sink.

        The notification is recorded separately. A chat platform being down must
        never undo an alert (§32).
        """
        require_authenticated(identity)
        deployments = DeploymentService(self.session, self.state, self.request_id, self.adapter)
        if deployment_id is not None:
            deployments.deployment(deployment_id)
            scoped = [deployment_id]
        else:
            scoped = None
        now = datetime.now(UTC)
        statuses: list[MonitoringExpectationStatus] = []
        for expectation in self.expectations(scoped):
            snapshots = self.snapshots(expectation.deployment_id)
            statuses.append(
                evaluate_expectation(
                    expectation=expectation,
                    last_snapshot=snapshots[-1] if snapshots else None,
                    now=now,
                    snapshot_count=len(snapshots),
                )
            )

        run = ExpectationEvaluationRun(
            evaluated_by=identity.actor_id,
            deployment_ids=sorted({item.deployment_id for item in statuses}),
            expectation_count=len(statuses),
            satisfied_count=sum(item.status == "satisfied" for item in statuses),
            due_soon_count=sum(item.status == "due_soon" for item in statuses),
            overdue_count=sum(item.status in ("overdue", "no_snapshot") for item in statuses),
            unknown_count=sum(item.status == "unknown" for item in statuses),
            telemetry_missing_count=sum("telemetry_missing" in item.findings for item in statuses),
            telemetry_late_count=sum("telemetry_late" in item.findings for item in statuses),
            statuses=statuses,
        )

        alerts = AlertService(self.session, self.state, self.request_id, self.adapter)
        for status in statuses:
            if not status.findings:
                continue
            deployment = deployments.deployment(status.deployment_id)
            for finding in status.findings:
                alert_id = self._raise_expectation_alert(
                    alerts,
                    finding=finding,
                    status=status,
                    deployment=deployment,
                    actor=identity.actor_id,
                )
                run.alert_ids.append(alert_id)
                if alert_id not in run.created_alert_ids:
                    run.created_alert_ids.append(alert_id)

        self.session.add(
            ExpectationEvaluationRunRow(
                evaluation_run_id=run.evaluation_run_id,
                evaluated_by=run.evaluated_by,
                expectation_count=run.expectation_count,
                overdue_count=run.overdue_count,
                policy_version=run.policy_version,
                run_json=run.model_dump(mode="json"),
                run_hash=canonical_hash(run),
                evaluated_at=utcnow(),
            )
        )
        for scoped_id in run.deployment_ids:
            deployment = deployments.deployment(scoped_id)
            self._event(
                deployment.metric_definition_id,
                "expectation_evaluated",
                actor=identity.actor_id,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": scoped_id,
                    "evaluation_run_id": run.evaluation_run_id,
                    "expectation_count": run.expectation_count,
                    "telemetry_missing_count": run.telemetry_missing_count,
                    "telemetry_late_count": run.telemetry_late_count,
                },
            )
            if run.telemetry_missing_count:
                self._event(
                    deployment.metric_definition_id,
                    "telemetry_missing_detected",
                    actor=identity.actor_id,
                    metric_version_id=deployment.metric_version_id,
                    release_id=deployment.release_id,
                    metadata={
                        "production_deployment_id": scoped_id,
                        "evaluation_run_id": run.evaluation_run_id,
                    },
                )
        self.session.commit()
        logger.info(
            "expectation_evaluated",
            extra={
                "actor_id": identity.actor_id,
                "evaluation_run_id": run.evaluation_run_id,
                "expectation_count": run.expectation_count,
                "overdue_count": run.overdue_count,
                "request_id": self.request_id,
            },
        )
        return run

    def _raise_expectation_alert(self, alerts, *, finding, status, deployment, actor) -> str:
        """One alert per (deployment, finding, missed window).

        The window key makes a re-run idempotent: evaluating the same window twice
        increments the existing alert instead of inventing a second one, while a
        genuinely new missed window opens its own.
        """
        dedup_key = telemetry_finding_dedup_key(
            deployment_id=status.deployment_id,
            finding=finding,
            window_key=status.window_key or "",
        )
        severity = "warning" if finding == "telemetry_late" else "critical"
        alert, _ = alerts.record(
            deployment=deployment,
            type="missing_monitoring_data",
            severity=severity,
            evidence={
                "finding": finding,
                "window_key": status.window_key,
                "status": status.status,
                "last_observation_time": (
                    None
                    if status.last_observation_time is None
                    else status.last_observation_time.isoformat()
                ),
                "due_at": None if status.due_at is None else status.due_at.isoformat(),
                "telemetry_source_id": status.telemetry_source_id,
            },
            recommended="investigate",
            actor=actor,
            dedup_key=dedup_key,
        )
        return alert.alert_id

    def expectations(self, deployment_ids=None):
        statement = select(MonitoringExpectationRow).order_by(MonitoringExpectationRow.created_at)
        if deployment_ids is not None:
            statement = statement.where(
                MonitoringExpectationRow.deployment_id.in_(sorted(deployment_ids))
            )
        return [
            decode(MonitoringExpectation, row.expectation_json)
            for row in self.session.scalars(statement)
        ]

    def evaluation_run(self, evaluation_run_id) -> ExpectationEvaluationRun:
        row = self.session.get(ExpectationEvaluationRunRow, evaluation_run_id)
        if row is None:
            raise ProductionNotFound("expectation_evaluation_run_not_found")
        run = decode(ExpectationEvaluationRun, row.run_json)
        if row.run_hash != canonical_hash(run):
            raise ProductionError("expectation_evaluation_run_changed")
        return run

    def evaluation_runs(self, deployment_id=None):
        rows = self.session.scalars(
            select(ExpectationEvaluationRunRow).order_by(ExpectationEvaluationRunRow.evaluated_at)
        )
        runs = [self.evaluation_run(row.evaluation_run_id) for row in rows]
        if deployment_id:
            runs = [run for run in runs if deployment_id in run.deployment_ids]
        return runs

    def telemetry_source_health(self, telemetry_source_id) -> TelemetrySourceHealth:
        """§29: the pipeline's operational state - never a verdict about the metric."""
        source = self.telemetry_source(telemetry_source_id)
        rows = self.session.scalars(
            select(MonitoringSnapshotRow)
            .where(MonitoringSnapshotRow.telemetry_source_id == telemetry_source_id)
            .order_by(MonitoringSnapshotRow.observation_time)
        )
        snapshots = [decode(MonitoringSnapshot, row.snapshot_json) for row in rows]
        now = datetime.now(UTC)
        overdue: list[str] = []
        silent: list[str] = []
        for expectation in self.expectations():
            if expectation.telemetry_source_id != telemetry_source_id:
                continue
            own = self.snapshots(expectation.deployment_id)
            status = evaluate_expectation(
                expectation=expectation,
                last_snapshot=own[-1] if own else None,
                now=now,
                snapshot_count=len(own),
            )
            if status.status == "no_snapshot":
                silent.append(expectation.deployment_id)
            elif "telemetry_missing" in status.findings:
                overdue.append(expectation.deployment_id)
        return telemetry_source_health(
            source=source,
            latest_snapshot=snapshots[-1] if snapshots else None,
            snapshot_count=len(snapshots),
            overdue_deployment_ids=overdue,
            silent_deployment_ids=silent,
            observed_at=now,
        )


class AlertService(_ProductionService):
    def record(
        self,
        *,
        deployment,
        type,
        severity,
        evidence,
        recommended,
        actor,
        dedup_key=None,
    ):
        dedup_key = dedup_key or finding_dedup_key(deployment.deployment_id, type, severity)
        policy_version = deployment.monitoring_policy.version
        now = datetime.now(UTC)
        row = self.session.scalar(
            select(MetricAlertRow).where(MetricAlertRow.dedup_key == dedup_key)
        )
        if row is None:
            alert = MetricAlert(
                deployment_id=deployment.deployment_id,
                metric_definition_id=deployment.metric_definition_id,
                metric_version_id=deployment.metric_version_id,
                metric_key=deployment.metric_key,
                type=type,
                severity=severity,
                evidence=evidence,
                dedup_key=dedup_key,
                occurrences=1,
                recommended_action=recommended,
                monitoring_policy_version=policy_version,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(
                MetricAlertRow(
                    alert_id=alert.alert_id,
                    deployment_id=alert.deployment_id,
                    metric_definition_id=alert.metric_definition_id,
                    metric_version_id=alert.metric_version_id,
                    type=alert.type,
                    severity=alert.severity,
                    status=alert.status,
                    dedup_key=alert.dedup_key,
                    occurrences=alert.occurrences,
                    recommended_action=alert.recommended_action,
                    monitoring_policy_version=alert.monitoring_policy_version,
                    alert_json=alert.model_dump(mode="json"),
                    alert_hash=metric_alert_hash(alert),
                    first_seen_at=utcnow(),
                    last_seen_at=utcnow(),
                    resolved_at=None,
                    created_at=utcnow(),
                )
            )
            self._event(
                deployment.metric_definition_id,
                "monitoring_alert_created",
                actor=actor,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "alert_id": alert.alert_id,
                    "type": alert.type,
                    "severity": alert.severity,
                    "recommended_action": alert.recommended_action,
                },
            )
            self._deliver(alert, actor=actor)
            return alert, True
        alert = decode(MetricAlert, row.alert_json)
        reopened = alert.status != "open"
        alert = self._replace(
            alert,
            status="open",
            occurrences=alert.occurrences + 1,
            evidence=evidence,
            severity=severity,
            last_seen_at=now,
            decision=None,
            reviewer=None,
            comment=None,
            resolved_at=None,
            monitoring_policy_version=policy_version,
        )
        row.status = alert.status
        row.occurrences = alert.occurrences
        row.severity = alert.severity
        row.recommended_action = alert.recommended_action
        row.monitoring_policy_version = alert.monitoring_policy_version
        row.alert_json = alert.model_dump(mode="json")
        row.alert_hash = metric_alert_hash(alert)
        row.last_seen_at = utcnow()
        row.resolved_at = None
        self.session.flush()
        if reopened:
            self._event(
                deployment.metric_definition_id,
                "monitoring_alert_created",
                actor=actor,
                metric_version_id=deployment.metric_version_id,
                release_id=deployment.release_id,
                metadata={
                    "production_deployment_id": deployment.deployment_id,
                    "alert_id": alert.alert_id,
                    "type": alert.type,
                    "severity": alert.severity,
                    "reopened": True,
                },
            )
            # A reopen is new evidence, so it is worth saying again. `resend=True`
            # makes that explicit rather than accidental.
            self._deliver(alert, actor=actor, resend=True)
        return alert, False

    def alerts(self, deployment_id=None, status=None):
        statement = select(MetricAlertRow).order_by(MetricAlertRow.created_at)
        if deployment_id:
            statement = statement.where(MetricAlertRow.deployment_id == deployment_id)
        if status:
            statement = statement.where(MetricAlertRow.status == status)
        return [self.alert(row.alert_id) for row in self.session.scalars(statement)]

    def alert(self, alert_id):
        row = self.session.get(MetricAlertRow, alert_id)
        if row is None:
            raise ProductionNotFound("metric_alert_not_found")
        alert = decode(MetricAlert, row.alert_json)
        if (
            row.dedup_key != alert.dedup_key
            or row.occurrences != alert.occurrences
            or row.status != alert.status
            or row.severity != alert.severity
        ):
            raise ProductionError("production_deployment_evidence_changed")
        return alert

    def decide(self, alert_id, payload: AlertDecision, identity: ActorIdentity):
        """Acknowledging an alert is never a rollback; nothing is executed here."""
        require_authenticated(identity)
        alert = self.alert(alert_id)
        target = {
            "acknowledge": "acknowledged",
            "resolve": "resolved",
            "dismiss": "dismissed",
        }[payload.decision]
        alert = self._replace(
            alert,
            status=target,
            decision=payload.decision,
            reviewer=identity.actor_id,
            comment=payload.comment,
            resolved_at=datetime.now(UTC) if target in ("resolved", "dismissed") else None,
        )
        row = self.session.get(MetricAlertRow, alert_id)
        row.status = alert.status
        row.alert_json = alert.model_dump(mode="json")
        row.alert_hash = metric_alert_hash(alert)
        row.resolved_at = (
            alert.resolved_at.astimezone(UTC).replace(tzinfo=None) if alert.resolved_at else None
        )
        self.session.flush()
        self.session.commit()
        logger.info(
            "metric_alert_decided",
            extra={
                "actor_id": identity.actor_id,
                "alert_id": alert_id,
                "status": target,
                "alert_severity": alert.severity,
                "request_id": self.request_id,
            },
        )
        return alert

    # ------------------------------------------------------- Phase 9: notify

    def _latest_notification(self, alert_id, sink) -> NotificationDelivery | None:
        row = self.session.scalar(
            select(NotificationDeliveryRow)
            .where(
                NotificationDeliveryRow.alert_id == alert_id,
                NotificationDeliveryRow.sink == sink,
            )
            .order_by(NotificationDeliveryRow.attempted_at.desc())
        )
        return None if row is None else decode(NotificationDelivery, row.delivery_json)

    def _deliver(self, alert, *, actor, resend=False) -> NotificationDelivery:
        """One explicit attempt by one sink for one alert.

        §32/§34: an alert and a notification have different failure modes. A sink
        that is down cannot undo the alert, so a failure is recorded as a fact about
        the sink and never raised. There is no background retry queue either - a
        retry is a second explicit call, made by a human or a scheduler.
        """
        sink = self._notification_sink()
        previous = self._latest_notification(alert.alert_id, sink.name)
        if previous is not None and previous.status == "delivered" and not resend:
            # The same alert is never sent twice by accident. The earlier delivery is
            # returned, marked so a caller can tell a replay from a fresh send.
            return self._replace(previous, replayed=True)
        result = sink.notify(alert)
        delivery = NotificationDelivery(
            alert_id=alert.alert_id,
            deployment_id=alert.deployment_id,
            sink=result.sink,
            status=result.status,
            attempted_by=actor,
            provider_message_id=result.provider_message_id,
            failure_category=result.failure_category,
            detail=result.detail,
        )
        self.session.add(
            NotificationDeliveryRow(
                notification_delivery_id=delivery.notification_delivery_id,
                alert_id=delivery.alert_id,
                deployment_id=delivery.deployment_id,
                sink=delivery.sink,
                status=delivery.status,
                provider_message_id=delivery.provider_message_id,
                failure_category=delivery.failure_category,
                delivery_json=delivery.model_dump(mode="json"),
                delivery_hash=canonical_hash(delivery),
                attempted_at=utcnow(),
            )
        )
        self._event(
            alert.metric_definition_id,
            "notification_attempted",
            actor=actor,
            metric_version_id=alert.metric_version_id,
            metadata={
                "production_deployment_id": alert.deployment_id,
                "alert_id": alert.alert_id,
                "notification_delivery_id": delivery.notification_delivery_id,
                "sink": delivery.sink,
                "status": delivery.status,
                "failure_category": delivery.failure_category,
            },
        )
        return delivery

    def notify(self, alert_id, identity: ActorIdentity, *, resend=False) -> NotificationDelivery:
        """Deliver an existing alert through the configured sink, on request."""
        require_authenticated(identity)
        delivery = self._deliver(self.alert(alert_id), actor=identity.actor_id, resend=resend)
        # `_deliver` deliberately does not commit, because `record()` is committed by
        # its own caller as part of a wider transaction. On this route *we* are that
        # caller, so the attempt is committed here - otherwise a requested retry would
        # be recorded inside a transaction nobody ever commits.
        self.session.commit()
        return delivery

    def notifications(self, alert_id) -> list[NotificationDelivery]:
        rows = self.session.scalars(
            select(NotificationDeliveryRow)
            .where(NotificationDeliveryRow.alert_id == alert_id)
            .order_by(NotificationDeliveryRow.attempted_at)
        )
        return [decode(NotificationDelivery, row.delivery_json) for row in rows]


# -------------------------------------------------------------------- feedback


ALERT_TO_FEEDBACK = {
    "execution_failure": "runtime_failure",
    "latency_degradation": "performance_degradation",
    "coverage_drop": "data_quality_degradation",
    "null_rate_increase": "data_quality_degradation",
    "duplicate_increase": "data_quality_degradation",
    "missing_monitoring_data": "data_quality_degradation",
    "population_shift": "distribution_drift",
    "deployment_state_mismatch": "distribution_drift",
}


class FeedbackService(_ProductionService):
    def record(self, payload: FeedbackRequest, identity: ActorIdentity):
        require_authenticated(identity)
        service = DeploymentService(self.session, self.state, self.request_id, self.adapter)
        deployment = service.deployment(payload.deployment_id)
        for ref in payload.evidence_refs:
            self._require_ref(ref)
        event = FeedbackEvent(
            metric_definition_id=deployment.metric_definition_id,
            metric_version_id=deployment.metric_version_id,
            deployment_id=deployment.deployment_id,
            source="manual",
            type=payload.type,
            severity=payload.severity,
            evidence_refs=list(payload.evidence_refs),
            note=payload.note,
            recorded_by=identity.actor_id,
        )
        self._persist(event, deployment)
        self.session.commit()
        return event

    def record_from_alert(self, alert_id, note, identity: ActorIdentity):
        require_authenticated(identity)
        alert = AlertService(self.session, self.state, self.request_id, self.adapter).alert(
            alert_id
        )
        deployment = DeploymentService(
            self.session, self.state, self.request_id, self.adapter
        ).deployment(alert.deployment_id)
        event = FeedbackEvent(
            metric_definition_id=alert.metric_definition_id,
            metric_version_id=alert.metric_version_id,
            deployment_id=alert.deployment_id,
            source="monitoring_alert",
            type=ALERT_TO_FEEDBACK[alert.type],
            severity=alert.severity,
            evidence_refs=[{"kind": "alert", "ref_id": alert.alert_id}],
            note=note,
            recorded_by=identity.actor_id,
        )
        self._persist(event, deployment)
        self.session.commit()
        return event

    def _persist(self, event, deployment):
        self.session.add(
            MetricFeedbackEventRow(
                feedback_event_id=event.feedback_event_id,
                metric_definition_id=event.metric_definition_id,
                metric_version_id=event.metric_version_id,
                deployment_id=event.deployment_id,
                source=event.source,
                type=event.type,
                severity=event.severity,
                recorded_by=event.recorded_by,
                event_json=event.model_dump(mode="json"),
                event_hash=feedback_event_hash(event),
                created_at=utcnow(),
            )
        )
        self._event(
            event.metric_definition_id,
            "feedback_recorded",
            actor=event.recorded_by,
            metric_version_id=event.metric_version_id,
            release_id=deployment.release_id,
            metadata={
                "production_deployment_id": event.deployment_id,
                "feedback_event_id": event.feedback_event_id,
                "type": event.type,
                "severity": event.severity,
                "source": event.source,
            },
        )
        logger.info(
            "metric_feedback_recorded",
            extra={
                "actor_id": event.recorded_by,
                "metric_definition_id": event.metric_definition_id,
                "metric_version_id": event.metric_version_id,
                "production_deployment_id": event.deployment_id,
                "feedback_event_id": event.feedback_event_id,
                "request_id": self.request_id,
            },
        )

    def _require_ref(self, ref):
        models = {
            "alert": MetricAlertRow,
            "monitoring_snapshot": MonitoringSnapshotRow,
            "deployment": ProductionDeploymentRow,
            "release": MetricReleaseRow,
            "metric_version": MetricVersionRow,
        }[ref.kind]
        if self.session.get(models, ref.ref_id) is None:
            raise ProductionNotFound("feedback_evidence_ref_not_found")

    def event(self, feedback_event_id):
        row = self.session.get(MetricFeedbackEventRow, feedback_event_id)
        if row is None:
            raise ProductionNotFound("feedback_event_not_found")
        event = decode(FeedbackEvent, row.event_json)
        if feedback_event_hash(event) != row.event_hash:
            raise ProductionError("production_deployment_evidence_changed")
        return event

    def events(self, deployment_id=None):
        statement = select(MetricFeedbackEventRow).order_by(MetricFeedbackEventRow.created_at)
        if deployment_id:
            statement = statement.where(MetricFeedbackEventRow.deployment_id == deployment_id)
        return [self.event(row.feedback_event_id) for row in self.session.scalars(statement)]

    def request_research(
        self, feedback_event_id, payload: ResearchRequestCreate, identity: ActorIdentity
    ):
        """Feedback becomes research only through this explicit human request."""
        require_authenticated(identity)
        event = self.event(feedback_event_id)
        request = FeedbackResearchRequest(
            feedback_event_id=event.feedback_event_id,
            metric_definition_id=event.metric_definition_id,
            metric_version_id=event.metric_version_id,
            request_type=payload.request_type,
            rationale=payload.rationale,
        )
        self.session.add(
            FeedbackResearchRequestRow(
                research_request_id=request.research_request_id,
                feedback_event_id=request.feedback_event_id,
                metric_definition_id=request.metric_definition_id,
                metric_version_id=request.metric_version_id,
                request_type=request.request_type,
                decision="pending",
                request_json=request.model_dump(mode="json"),
                request_hash=canonical_hash(request),
                created_at=utcnow(),
                reviewed_at=None,
            )
        )
        self.session.commit()
        return request

    def research_request(self, research_request_id):
        row = self.session.get(FeedbackResearchRequestRow, research_request_id)
        if row is None:
            raise ProductionNotFound("feedback_research_request_not_found")
        request = decode(FeedbackResearchRequest, row.request_json)
        if row.decision != request.decision:
            raise ProductionError("production_deployment_evidence_changed")
        return request

    def decide_research(
        self, research_request_id, payload: FeedbackResearchDecision, identity: ActorIdentity
    ):
        """Records the human decision. It never starts a reflection or experiment."""
        authorize(identity, "decide_feedback_research")
        request = self.research_request(research_request_id)
        if request.decision != "pending":
            raise ProductionError("feedback_research_already_decided")
        updated = self._replace(
            request,
            decision=payload.decision,
            reviewer=identity.actor_id,
            reviewer_actor_id=identity.actor_id,
            comment=payload.comment,
            reviewed_at=datetime.now(UTC),
        )
        row = self.session.get(FeedbackResearchRequestRow, research_request_id)
        row.decision = updated.decision
        row.request_json = updated.model_dump(mode="json")
        row.request_hash = canonical_hash(updated)
        row.reviewed_at = utcnow()
        self.session.flush()
        self.session.commit()
        return updated


class DeploymentReconciler:
    """Queries registry versus runtime and names the result. It never repairs."""

    def __init__(self, service: DeploymentService):
        self.service = service

    def reconcile(self, deployment_id, identity):
        return self.service.reconcile(deployment_id, identity)


def package_integrity_error(*failures):
    raise PackageIntegrityError(",".join(sorted(set(failures))))

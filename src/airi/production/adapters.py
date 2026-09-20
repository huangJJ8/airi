"""Provider boundary for production runtimes.

Business code never imports Spark, Hive, Argo, Kubernetes or a scheduler. It
depends on :class:`ProductionAdapter` only. The `validate` / `deploy` / `status` /
`rollback` methods are the mandated contract; `observe` carries the read-only
production shadow.

No adapter here ever fabricates a provider identifier. When a job id, a runtime
identity or an engine version is not available it stays ``None`` and the runtime
identity stays unverified - which is what keeps ``production_deployed`` honest.

Phase 8 makes :class:`SparkProductionAdapter` real: it probes the cluster, runs
the shadow read-only, and drives the platform's activation ledger when one is
configured. A platform with connectivity but no control channel reports
NOT VERIFIED for deployment rather than inferring success from a read-only query.
"""

from typing import Protocol

from sqlalchemy import select

from airi.approvals.persistence import ApprovalRecordRow
from airi.core.exceptions import AIRIError
from airi.execution.models import ExecutionRequest
from airi.execution.service import ExecutionService
from airi.production.models import (
    DeploymentPackage,
    PreflightCheck,
    ProductionDeploymentResult,
    ProductionDeploymentStatus,
    ProductionRollbackResult,
    ProductionShadowObservation,
    ProductionValidationResult,
    ProviderActivation,
    RuntimeMode,
)
from airi.production.monitoring import bins_from_values
from airi.production.probe import ThriftSparkConnection, run_production_probe
from airi.registry.shadow import metric_values


class ProductionAdapterError(AIRIError):
    code = "production_adapter_unavailable"
    status_code = 503


class ProductionAdapter(Protocol):
    """The only production surface the business layer may depend on."""

    name: str
    authoritative: bool
    runtime_mode: RuntimeMode
    supports_shadow: bool
    supports_rollback: bool

    def validate(self, package: DeploymentPackage) -> ProductionValidationResult: ...

    def observe(self, package: DeploymentPackage) -> ProductionShadowObservation: ...

    def deploy(
        self, package: DeploymentPackage, deployment_id: str
    ) -> ProductionDeploymentResult: ...

    def status(self, deployment_id: str) -> ProductionDeploymentStatus: ...

    def rollback(
        self, deployment_id: str, target_version_id: str | None = None
    ) -> ProductionRollbackResult: ...


class DisabledProductionAdapter:
    """Default outside tests. Every production action fails closed."""

    name = "disabled"
    authoritative = False
    supports_shadow = False
    supports_rollback = False
    runtime_mode: RuntimeMode = "disabled"

    def _refuse(self, detail: str):
        raise ProductionAdapterError(detail)

    def validate(self, package: DeploymentPackage) -> ProductionValidationResult:
        return ProductionValidationResult(
            status="not_verified",
            checks=[
                PreflightCheck(
                    name="adapter",
                    status="not_verified",
                    detail="No production adapter is configured",
                )
            ],
            read_only=True,
            detail="production adapter disabled",
        )

    def observe(self, package: DeploymentPackage) -> ProductionShadowObservation:
        return ProductionShadowObservation(
            execution_success=False,
            failure_category="production_adapter_disabled",
            detail="production adapter disabled",
            read_only_basis="not_verified",
            synthetic_data=True,
        )

    def deploy(self, package: DeploymentPackage, deployment_id: str) -> ProductionDeploymentResult:
        return ProductionDeploymentResult(
            status="not_verified", detail="production adapter disabled"
        )

    def status(self, deployment_id: str) -> ProductionDeploymentStatus:
        return ProductionDeploymentStatus(
            deployment_id=deployment_id,
            runtime_state="unknown",
            detail="production adapter disabled",
        )

    def rollback(
        self, deployment_id: str, target_version_id: str | None = None
    ) -> ProductionRollbackResult:
        return ProductionRollbackResult(status="not_verified", detail="production adapter disabled")


class MockProductionAdapter:
    """Explicit test/demo double. Simulates a provider; is never authoritative.

    Reporting ``deployed`` advances the governance state machine, but the runtime
    identity stays unverified and no provider identifier is invented, so
    ``production_deployed`` remains ``False`` on the deployment.
    """

    name = "mock"
    authoritative = False
    supports_shadow = True
    supports_rollback = True
    runtime_mode: RuntimeMode = "mock"

    def __init__(
        self,
        session,
        state,
        request_id: str | None = None,
        *,
        runtime_store: dict | None = None,
        fail_shadow: bool = False,
        fail_deploy: bool = False,
        fail_rollback: bool = False,
    ):
        self.session, self.state, self.request_id = session, state, request_id
        self.runtime = runtime_store if runtime_store is not None else {}
        self.fail_shadow, self.fail_deploy, self.fail_rollback = (
            fail_shadow,
            fail_deploy,
            fail_rollback,
        )

    def validate(self, package: DeploymentPackage) -> ProductionValidationResult:
        approved = self.session.scalar(
            select(ApprovalRecordRow).where(
                ApprovalRecordRow.artifact_id == package.artifact_id,
                ApprovalRecordRow.decision == "approved",
            )
        )
        checks = [
            PreflightCheck(
                name="read_only_session",
                status="passed",
                detail="Mock adapter performs no writes and no business output",
            ),
            PreflightCheck(
                name="governed_artifact_approval",
                status="passed" if approved else "failed",
                detail=(
                    "Approved artifact is available to the provider"
                    if approved
                    else "No approved SQL decision for this artifact"
                ),
            ),
            PreflightCheck(
                name="runtime_identity",
                status="not_verified",
                detail="Mock provider exposes no job identity",
            ),
        ]
        return ProductionValidationResult(
            status="passed" if approved else "failed",
            checks=checks,
            read_only=True,
            detail="synthetic mock provider",
        )

    def observe(self, package: DeploymentPackage) -> ProductionShadowObservation:
        if self.fail_shadow:
            return ProductionShadowObservation(
                execution_success=False,
                failure_category="synthetic_provider_failure",
                detail="Injected mock provider failure",
                read_only_basis="synthetic_provider",
                synthetic_data=True,
            )
        approval = self.session.scalar(
            select(ApprovalRecordRow).where(
                ApprovalRecordRow.artifact_id == package.artifact_id,
                ApprovalRecordRow.decision == "approved",
            )
        )
        if approval is None:
            return ProductionShadowObservation(
                execution_success=False,
                failure_category="release_artifact_not_approved",
                detail="No approved SQL decision for this artifact",
                read_only_basis="synthetic_provider",
                synthetic_data=True,
            )
        try:
            executed, _, _ = ExecutionService(self.session, self.state.query_executor).run(
                ExecutionRequest(
                    artifact_id=package.artifact_id, approval_id=approval.approval_id, max_rows=1000
                ),
                self.request_id,
            )
        except AIRIError as exc:
            return ProductionShadowObservation(
                execution_success=False,
                failure_category=exc.code,
                detail="Shadow execution refused",
                read_only_basis="synthetic_provider",
                synthetic_data=True,
            )
        values, duplicates = metric_values(executed.rows, package.metric_ir.name)
        if executed.status != "success":
            return ProductionShadowObservation(
                execution_success=False,
                failure_category=executed.failure_category,
                row_count=executed.row_count,
                latency_ms=round(executed.duration_ms),
                detail="Shadow execution failed",
                read_only_basis="synthetic_provider",
                synthetic_data=True,
            )
        non_null = sum(value is not None for value in values.values())
        return ProductionShadowObservation(
            execution_success=True,
            provider_query_id=executed.provider_query_id,
            columns=[column.name for column in executed.columns],
            row_count=executed.row_count,
            entity_count=len(values),
            non_null_count=non_null,
            null_count=len(values) - non_null,
            duplicate_entities=duplicates,
            latency_ms=round(executed.duration_ms),
            bins=list(bins_from_values(values.values()).bins),
            read_only_basis="synthetic_provider",
            write_business_output=False,
            synthetic_data=True,
        )

    def deploy(self, package: DeploymentPackage, deployment_id: str) -> ProductionDeploymentResult:
        if self.fail_deploy:
            return ProductionDeploymentResult(
                status="failed",
                failure_category="synthetic_provider_failure",
                detail="Injected mock provider failure",
            )
        self.runtime[deployment_id] = {
            "metric_version_id": package.metric_version_id,
            "state": "active",
        }
        return ProductionDeploymentResult(
            status="deployed",
            runtime_identity_verified=False,
            detail="Synthetic mock runtime; no provider job identity exists",
        )

    def status(self, deployment_id: str) -> ProductionDeploymentStatus:
        record = self.runtime.get(deployment_id)
        if record is None:
            return ProductionDeploymentStatus(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="Mock provider has no record of this deployment",
            )
        return ProductionDeploymentStatus(
            deployment_id=deployment_id,
            runtime_state=record["state"],
            active_version_id=record.get("metric_version_id")
            if record["state"] == "active"
            else None,
            detail="Synthetic mock runtime",
        )

    def rollback(
        self, deployment_id: str, target_version_id: str | None = None
    ) -> ProductionRollbackResult:
        if self.fail_rollback:
            return ProductionRollbackResult(
                status="failed",
                failure_category="synthetic_provider_failure",
                detail="Injected mock provider rollback failure",
            )
        record = self.runtime.get(deployment_id)
        if record is None:
            return ProductionRollbackResult(
                status="not_verified", detail="Mock provider has no record of this deployment"
            )
        # A rollback with a target leaves the runtime running the *target*, which
        # is what makes a post-rollback convergence check meaningful.
        if target_version_id:
            record.update({"metric_version_id": target_version_id, "state": "active"})
            return ProductionRollbackResult(
                status="rolled_back",
                detail="Synthetic mock runtime now runs the target version",
            )
        record["state"] = "rolled_back"
        return ProductionRollbackResult(
            status="rolled_back", detail="Synthetic mock runtime stopped"
        )


class SparkProductionAdapter:
    """Real provider integration: probe, read-only shadow, activation ledger.

    Deployment control requires an explicit activation ledger. Everything the
    cluster cannot answer - runtime identity on a NOSASL session, a provider job
    id, a cancel capability - is reported as unverified, so this adapter can
    never on its own produce ``production_deployed = true``.
    """

    name = "spark_production"
    authoritative = True
    supports_shadow = True
    runtime_mode: RuntimeMode = "spark_production"

    def __init__(self, session, state, request_id: str | None = None, *, connection_factory=None):
        self.session, self.state, self.request_id = session, state, request_id
        self.settings = getattr(state, "settings", None)
        self._connection_factory = connection_factory or self._default_connection_factory
        self._connection = None
        self._last_probe = None

    # ------------------------------------------------------------- lifecycle

    def _default_connection_factory(self):
        if self.settings is None or not self.configured:
            raise ProductionAdapterError("production adapter not configured")
        return ThriftSparkConnection(self.settings)

    @property
    def configured(self) -> bool:
        settings = self.settings
        if settings is None:
            return False
        return bool(
            getattr(settings, "spark_production_host", "")
            and getattr(settings, "spark_production_username", "")
        )

    @property
    def supports_activation(self) -> bool:
        return bool(getattr(self.settings, "spark_production_activation_ledger", ""))

    @property
    def supports_rollback(self) -> bool:
        return self.configured and self.supports_activation

    def _require(self) -> None:
        if not self.configured:
            raise ProductionAdapterError("production adapter not configured")
        if getattr(self.settings, "environment", None) == "production":
            return
        # A production adapter outside production mode is only ever a test seam.
        if not getattr(self.settings, "spark_production_host", ""):
            raise ProductionAdapterError("production adapter not configured")

    def connection(self):
        if self._connection is None:
            self._connection = self._connection_factory()
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def probe(self, environment_id: str):
        probe = run_production_probe(self.connection(), environment_id)
        self._last_probe = probe
        return probe

    def _fingerprint(self, probe, environment_id: str):
        if not probe.reachable:
            return None
        return probe.fingerprint(
            environment_id=environment_id,
            read_only_attested=bool(
                getattr(self.settings, "spark_production_read_only_attested", False)
            ),
        )

    # ------------------------------------------------------------- preflight

    def validate(self, package: DeploymentPackage) -> ProductionValidationResult:
        self._require()
        probe = self.probe(package.environment_id)
        fingerprint = self._fingerprint(probe, package.environment_id)

        def capability(name: str, ok: bool, detail: str) -> PreflightCheck:
            return PreflightCheck(
                name=name, status="passed" if ok else "not_verified", detail=detail
            )

        checks = [
            PreflightCheck(
                name="provider_connectivity",
                status="passed" if probe.reachable else "failed",
                detail=probe.detail or "production runtime connectivity probe",
            ),
            PreflightCheck(
                name="read_only_session",
                status="passed" if probe.read_only_capability == "verified" else "not_verified",
                detail=f"read_only_capability={probe.read_only_capability}",
            ),
            capability(
                "runtime_identity",
                probe.runtime_identity_verified,
                "runtime_identity="
                + ("verified" if probe.runtime_identity_verified else "not_verified"),
            ),
            capability(
                "provider_authentication",
                probe.authenticated,
                f"auth_mode={probe.auth_mode}; authenticated={probe.authenticated}",
            ),
            capability(
                "query_id",
                bool(probe.query_id_available),
                f"query_id_available={probe.query_id_available}",
            ),
            capability(
                "cancel",
                probe.cancel_capability == "verified",
                f"cancel_capability={probe.cancel_capability}",
            ),
            capability(
                "activation_control_channel",
                bool(fingerprint is not None and self._connection_supports_activation()),
                "production activation control channel is configured"
                if self._connection_supports_activation()
                else "no production activation control channel is configured",
            ),
        ]
        return ProductionValidationResult(
            status="passed" if probe.reachable else "failed",
            checks=checks,
            provider_query_id=probe.provider_query_id,
            read_only=True,
            authoritative=True,
            runtime_identity=probe.runtime_identity,
            runtime_identity_verified=probe.runtime_identity_verified,
            environment_fingerprint_hash=None
            if fingerprint is None
            else fingerprint.fingerprint_hash,
            auth_mode=probe.auth_mode,
            detail=(
                "production runtime probed"
                if probe.reachable
                else "production runtime is not reachable"
            ),
        )

    def _connection_supports_activation(self) -> bool:
        try:
            return bool(self.connection().supports_activation)
        except Exception:  # noqa: BLE001 - an unbuildable connection is not configured
            return False

    # ---------------------------------------------------- read-only shadow

    def observe(self, package: DeploymentPackage) -> ProductionShadowObservation:
        self._require()
        probe = self.probe(package.environment_id)
        fingerprint = self._fingerprint(probe, package.environment_id)
        read_only_basis = (
            "read_only_identity" if probe.read_only_capability == "verified" else "not_verified"
        )
        if not probe.reachable:
            return ProductionShadowObservation(
                execution_success=False,
                failure_category=probe.failure_category or "production_runtime_unreachable",
                detail="production runtime is not reachable",
                read_only_basis="not_verified",
                synthetic_data=False,
            )
        result = self.connection().run_query(
            package.sql,
            max_rows=1000,
            timeout_seconds=int(
                getattr(self.settings, "spark_production_query_timeout_seconds", 60)
            ),
        )
        if result.status != "success":
            return ProductionShadowObservation(
                execution_success=False,
                failure_category=result.failure_category or "execution_error",
                latency_ms=result.duration_ms,
                provider_query_id=result.provider_query_id,
                runtime_identity=probe.runtime_identity,
                environment_fingerprint_hash=None
                if fingerprint is None
                else fingerprint.fingerprint_hash,
                read_only_basis=read_only_basis,
                detail="production shadow query failed",
                synthetic_data=False,
            )
        values, duplicates = metric_values(result.rows, package.metric_ir.name)
        non_null = sum(value is not None for value in values.values())
        return ProductionShadowObservation(
            execution_success=True,
            provider_query_id=result.provider_query_id,
            runtime_identity=probe.runtime_identity,
            environment_fingerprint_hash=None
            if fingerprint is None
            else fingerprint.fingerprint_hash,
            columns=list(result.columns),
            row_count=len(result.rows),
            entity_count=len(values),
            non_null_count=non_null,
            null_count=len(values) - non_null,
            duplicate_entities=duplicates,
            latency_ms=result.duration_ms,
            bins=list(bins_from_values(values.values()).bins),
            read_only_basis=read_only_basis,
            write_business_output=False,
            synthetic_data=False,
        )

    # -------------------------------------------------------------- deploy

    def deploy(self, package: DeploymentPackage, deployment_id: str) -> ProductionDeploymentResult:
        self._require()
        if not self._connection_supports_activation():
            return ProductionDeploymentResult(
                status="not_verified",
                failure_category="activation_control_channel_unavailable",
                detail="no production activation control channel is configured",
            )
        probe = self.probe(package.environment_id)
        fingerprint = self._fingerprint(probe, package.environment_id)
        if not probe.reachable:
            return ProductionDeploymentResult(
                status="failed",
                failure_category=probe.failure_category or "production_runtime_unreachable",
                detail="production runtime is not reachable",
            )
        if (
            package.environment_fingerprint_hash is not None
            and fingerprint is not None
            and fingerprint.fingerprint_hash != package.environment_fingerprint_hash
        ):
            # Preflight may have run against a different cluster than this one.
            return ProductionDeploymentResult(
                status="failed",
                failure_category="environment_fingerprint_mismatch",
                detail="runtime does not match the deployment package environment",
            )
        activation = self._activate(deployment_id, package.metric_version_id)
        if activation.runtime_state != "active":
            return ProductionDeploymentResult(
                status="failed",
                failure_category="activation_control_channel_failed",
                detail=activation.detail or "provider did not confirm the activation",
                runtime_identity=probe.runtime_identity,
                runtime_identity_verified=probe.runtime_identity_verified,
            )
        return ProductionDeploymentResult(
            status="deployed",
            provider_job_id=activation.provider_job_id,
            provider_query_id=activation.provider_query_id,
            provider_application_id=activation.provider_application_id,
            runtime_identity=probe.runtime_identity,
            runtime_identity_verified=probe.runtime_identity_verified,
            environment_fingerprint_hash=None
            if fingerprint is None
            else fingerprint.fingerprint_hash,
            detail="provider confirmed the activation",
        )

    def _activate(self, deployment_id: str, metric_version_id: str) -> ProviderActivation:
        return self.connection().activate_metric(
            deployment_id=deployment_id, metric_version_id=metric_version_id
        )

    def _deactivate(self, deployment_id: str, target_version_id: str) -> ProviderActivation:
        return self.connection().deactivate_metric(
            deployment_id=deployment_id, target_version_id=target_version_id
        )

    # -------------------------------------------------------------- status

    def status(self, deployment_id: str) -> ProductionDeploymentStatus:
        if not self.configured:
            return ProductionDeploymentStatus(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="production adapter not configured",
            )
        if not self._connection_supports_activation():
            return ProductionDeploymentStatus(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="no production activation control channel is configured",
            )
        activations = self.connection().active_metric_versions()
        match = next((item for item in activations if item.deployment_id == deployment_id), None)
        if match is None:
            return ProductionDeploymentStatus(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="the provider's activation ledger has no record of this deployment",
            )
        return ProductionDeploymentStatus(
            deployment_id=deployment_id,
            runtime_state=match.runtime_state,
            active_version_id=match.metric_version_id if match.runtime_state == "active" else None,
            provider_job_id=match.provider_job_id,
            provider_application_id=match.provider_application_id,
            provider_query_id=match.provider_query_id,
            detail=match.detail,
        )

    # ------------------------------------------------------------ rollback

    def rollback(
        self, deployment_id: str, target_version_id: str | None = None
    ) -> ProductionRollbackResult:
        self._require()
        if not target_version_id:
            return ProductionRollbackResult(
                status="not_verified",
                failure_category="rollback_target_missing",
                detail="a production rollback must name a target version",
            )
        if not self._connection_supports_activation():
            return ProductionRollbackResult(
                status="failed",
                failure_category="activation_control_channel_unavailable",
                detail="no production activation control channel is configured",
            )
        activation = self._deactivate(deployment_id, target_version_id)
        if (
            activation.runtime_state != "active"
            or activation.metric_version_id != target_version_id
        ):
            return ProductionRollbackResult(
                status="failed",
                failure_category="rollback_not_confirmed",
                detail=activation.detail or "provider did not confirm the rollback",
            )
        # §49: the provider's own status is re-read after the rollback.
        status = self.status(deployment_id)
        return ProductionRollbackResult(
            status="rolled_back",
            provider_job_id=activation.provider_job_id,
            provider_application_id=activation.provider_application_id,
            provider_query_id=activation.provider_query_id,
            runtime_identity=status.runtime_identity,
            runtime_identity_verified=bool(
                status.runtime_identity_verified or status.active_version_id == target_version_id
            ),
            detail=(
                "provider confirmed the rollback and reported the target as active"
                if status.active_version_id == target_version_id
                else "provider confirmed the rollback; runtime state was not re-read"
            ),
        )


def adapter_factory(config, *, runtime_store=None, connection_factory=None):
    """Resolve the configured adapter as a uniform per-call constructor.

    Always returns a callable ``(session, state, request_id) -> ProductionAdapter``
    so the service layer never has to know which adapter shape was configured.
    Production never installs a mock: asking for one in production yields a
    factory that fails closed when it is actually used.
    """

    if config.environment == "production" and config.production_adapter == "mock":

        def _forbidden(session, state, request_id=None):
            raise ProductionAdapterError("mock production adapter is forbidden in production")

        return _forbidden

    if config.production_adapter == "spark_production":

        def _spark(session, state, request_id=None):
            return SparkProductionAdapter(
                session, state, request_id, connection_factory=connection_factory
            )

        return _spark

    if config.production_adapter == "mock" and config.environment != "production":

        def _mock(session, state, request_id=None):
            return MockProductionAdapter(session, state, request_id, runtime_store=runtime_store)

        return _mock

    def _disabled(session, state, request_id=None):  # noqa: ARG001
        return DisabledProductionAdapter()

    return _disabled

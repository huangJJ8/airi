"""Phase 8 production verification report.

One aggregate "production_verified = true" would hide exactly the thing an
operator needs to see: *which* part of the production path is still unverified.
This module produces one verdict per section, then a policy-gated aggregate.

The report is evidence, not a gate: nothing here changes a deployment. The
admission gate lives in :class:`~airi.production.models.ProductionVerificationPolicy`
and is versioned, never client-supplied.
"""

from airi.production.models import (
    VERIFICATION_REQUIRED_SECTIONS,
    ProductionDeployment,
    ProductionDeploymentEvidence,
    ProductionEnvironmentFingerprint,
    ProductionEnvironmentProfile,
    ProductionRollbackReview,
    ProductionVerificationPolicy,
    ProductionVerificationReport,
    TrustedTelemetrySource,
    VerificationSection,
    verification_report_hash,
)
from airi.production.packaging import environment_profile_hash

VERIFIED = "verified"
PARTIAL = "partially_verified"
NOT_VERIFIED = "not_verified"
FAILED = "failed"


def build_verification_report(
    *,
    deployment: ProductionDeployment,
    profile: ProductionEnvironmentProfile,
    fingerprint: ProductionEnvironmentFingerprint | None,
    evidence: ProductionDeploymentEvidence | None,
    telemetry_source: TrustedTelemetrySource | None,
    rollback_review: ProductionRollbackReview | None,
    adapter_name: str,
    identity_trusted: bool,
    decision_actors_trusted: bool,
    policy: ProductionVerificationPolicy | None = None,
) -> ProductionVerificationReport:
    """Assemble the per-section report. Pure: it only reads and names.

    ``identity_trusted`` and ``decision_actors_trusted`` are supplied by the
    service because only it knows which trusted provider resolved the request.
    """
    policy = policy or ProductionVerificationPolicy()
    sections = [
        _environment_section(deployment, profile, fingerprint),
        _identity_section(identity_trusted, decision_actors_trusted, deployment),
        _adapter_section(adapter_name, deployment),
        _deployment_section(evidence),
        _runtime_identity_section(deployment),
        _shadow_section(deployment),
        _monitoring_source_section(telemetry_source),
        _rollback_section(rollback_review, policy),
        _reconciliation_section(deployment),
    ]
    by_name = {section.name: section for section in sections}
    required = [by_name[name] for name in VERIFICATION_REQUIRED_SECTIONS]
    blocking = sorted(section.name for section in required if section.status != VERIFIED)
    if policy.require_rollback_verified and by_name["rollback"].status != VERIFIED:
        blocking.append("rollback")
        blocking = sorted(set(blocking))
    not_verified = sorted(name for name, section in by_name.items() if section.status != VERIFIED)

    if any(section.status == FAILED for section in required):
        overall = FAILED
    elif not blocking:
        overall = VERIFIED
    elif any(section.status == VERIFIED for section in sections):
        overall = PARTIAL
    else:
        overall = NOT_VERIFIED

    report = ProductionVerificationReport(
        deployment_id=deployment.deployment_id,
        metric_definition_id=deployment.metric_definition_id,
        metric_version_id=deployment.metric_version_id,
        environment_id=deployment.environment_id,
        policy=policy,
        sections=sections,
        overall=overall,
        production_runtime_verified=not blocking,
        production_deployed=deployment.production_deployed,
        not_verified=not_verified,
        blocking=blocking,
        report_hash="0" * 64,
    )
    return report.model_copy(update={"report_hash": verification_report_hash(report)})


# ------------------------------------------------------------------- sections


def _section(name, status, detail, **evidence) -> VerificationSection:
    return VerificationSection(name=name, status=status, detail=detail, evidence=evidence)


def _environment_section(deployment, profile, fingerprint) -> VerificationSection:
    if not profile.deployable:
        return _section(
            "environment",
            FAILED,
            "the production environment is not deployable",
            verified=profile.verified,
            deployment_enabled=profile.deployment_enabled,
        )
    if profile.synthetic_profile:
        return _section(
            "environment",
            NOT_VERIFIED,
            "synthetic environment profile; no real cluster was verified",
            verification_source=profile.verification_source,
        )
    if fingerprint is None:
        return _section("environment", NOT_VERIFIED, "no runtime fingerprint has been observed")
    if not fingerprint.verified:
        return _section(
            "environment", NOT_VERIFIED, "the observed runtime fingerprint is not verified"
        )
    if fingerprint.fingerprint_hash != deployment.package.environment_fingerprint_hash:
        return _section(
            "environment",
            FAILED,
            "the observed runtime does not match the deployment package environment",
            observed=fingerprint.fingerprint_hash,
            packaged=deployment.package.environment_fingerprint_hash,
        )
    return _section(
        "environment",
        VERIFIED,
        "the runtime fingerprint matches the deployment package",
        fingerprint_hash=fingerprint.fingerprint_hash,
        environment_profile_hash=environment_profile_hash(profile),
    )


def _identity_section(identity_trusted, decision_actors_trusted, deployment) -> VerificationSection:
    if not identity_trusted:
        return _section("identity", NOT_VERIFIED, "no trusted identity boundary is configured")
    if not decision_actors_trusted:
        return _section(
            "identity",
            NOT_VERIFIED,
            "deployment decisions were not recorded under a trusted identity",
        )
    return _section(
        "identity",
        VERIFIED,
        "decisions were authorised against a trusted identity provider",
        requested_by=deployment.requested_by,
    )


def _adapter_section(adapter_name, deployment) -> VerificationSection:
    if deployment.runtime_mode != "spark_production":
        return _section(
            "adapter",
            NOT_VERIFIED,
            f"runtime_mode={deployment.runtime_mode}; no real production adapter is active",
            adapter=adapter_name,
        )
    return _section(
        "adapter",
        VERIFIED,
        "an authoritative production adapter is active",
        adapter=adapter_name,
    )


def _deployment_section(evidence) -> VerificationSection:
    if evidence is None:
        return _section("deployment", NOT_VERIFIED, "no deployment evidence was recorded")
    if evidence.deploy_status != "deployed":
        return _section(
            "deployment",
            FAILED,
            f"the provider reported {evidence.deploy_status}",
            adapter=evidence.adapter,
        )
    if not evidence.status_confirmed:
        return _section(
            "deployment",
            FAILED,
            "the provider's status did not confirm the deployment response",
            status_runtime_state=evidence.status_runtime_state,
        )
    if not evidence.production_deployed:
        return _section(
            "deployment",
            NOT_VERIFIED,
            "the deployment is confirmed by the provider but production was not claimed",
            adapter=evidence.adapter,
        )
    return _section(
        "deployment",
        VERIFIED,
        "the provider confirmed the deployment through deploy and status",
        provider_job_id=evidence.provider_job_id,
        provider_application_id=evidence.provider_application_id,
        provider_query_id=evidence.provider_query_id,
    )


def _runtime_identity_section(deployment) -> VerificationSection:
    if not deployment.runtime_identity_verified:
        return _section(
            "runtime_identity", NOT_VERIFIED, "the production runtime identity is not verified"
        )
    return _section(
        "runtime_identity",
        VERIFIED,
        "the production runtime reported a verified identity",
        runtime_identity=deployment.runtime_identity,
    )


def _shadow_section(deployment) -> VerificationSection:
    shadow = deployment.shadow
    if shadow is None:
        return _section("shadow", NOT_VERIFIED, "no production shadow was recorded")
    if shadow.status not in ("passed", "passed_with_warnings"):
        return _section("shadow", FAILED, f"the shadow verdict was {shadow.status}")
    if shadow.read_only_basis == "not_verified":
        return _section(
            "shadow",
            NOT_VERIFIED,
            "the shadow ran without a verified read-only basis",
        )
    return _section(
        "shadow",
        VERIFIED,
        "the shadow ran read-only against the production runtime",
        read_only_basis=shadow.read_only_basis,
        provider_query_id=shadow.provider_query_id,
        row_count=shadow.row_count,
    )


def _monitoring_source_section(telemetry_source) -> VerificationSection:
    if telemetry_source is None:
        return _section("monitoring_source", NOT_VERIFIED, "no trusted telemetry source is bound")
    if telemetry_source.synthetic:
        return _section(
            "monitoring_source",
            NOT_VERIFIED,
            "telemetry comes from a synthetic source",
            telemetry_source_id=telemetry_source.telemetry_source_id,
        )
    if not telemetry_source.verified:
        return _section(
            "monitoring_source",
            NOT_VERIFIED,
            "the bound telemetry source is not verified",
            telemetry_source_id=telemetry_source.telemetry_source_id,
        )
    return _section(
        "monitoring_source",
        VERIFIED,
        "telemetry is bound to a verified source in this environment",
        telemetry_source_id=telemetry_source.telemetry_source_id,
        source_system=telemetry_source.source_system,
        schema_version=telemetry_source.schema_version,
    )


def _rollback_section(review, policy) -> VerificationSection:
    if review is None or review.decision != "approved":
        return _section(
            "rollback",
            NOT_VERIFIED,
            "no approved production rollback has been executed",
            required=policy.require_rollback_verified,
        )
    if not review.runtime_verified:
        return _section(
            "rollback",
            NOT_VERIFIED,
            "the rollback ran but the provider did not confirm the runtime state",
            rollback_review_id=review.rollback_review_id,
        )
    if review.registry_reconciliation_status != "reconciled":
        return _section(
            "rollback",
            NOT_VERIFIED,
            "the rollback did not reconcile the registry pointer",
            registry_reconciliation_status=review.registry_reconciliation_status,
        )
    return _section(
        "rollback",
        VERIFIED,
        "an approved production rollback executed and reconciled",
        rollback_review_id=review.rollback_review_id,
        provider_job_id=review.provider_job_id,
    )


def _reconciliation_section(deployment) -> VerificationSection:
    reconciliation = deployment.reconciliation
    if reconciliation is None:
        return _section("reconciliation", NOT_VERIFIED, "no reconciliation has been observed")
    if reconciliation.status == "consistent":
        return _section(
            "reconciliation", VERIFIED, "registry and runtime agree", verdict="consistent"
        )
    return _section(
        "reconciliation",
        NOT_VERIFIED if reconciliation.status == "unknown" else FAILED,
        f"registry and runtime are {reconciliation.status}",
        registry_active_version_id=reconciliation.registry_active_version_id,
        production_active_version_id=reconciliation.production_active_version_id,
    )

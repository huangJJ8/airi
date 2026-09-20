"""Deterministic DeploymentPackage assembly and integrity verification.

The package is the only thing a production adapter may execute. It is derived
from the registry lineage, never from a request body: a client names a release
and an environment, and the SQL comes back from the governed artifact.
"""

import hashlib

from airi.approvals.artifacts import DevelopmentArtifact, artifact_content_hash, canonical_hash
from airi.approvals.persistence import DevelopmentArtifactRow
from airi.core.exceptions import AIRIError
from airi.production.models import (
    DeploymentPackage,
    ProductionEnvironmentFingerprint,
    ProductionEnvironmentProfile,
    deployment_package_hash,
)


class PackageIntegrityError(AIRIError):
    code = "deployment_package_integrity_error"
    status_code = 409


def environment_profile_hash(profile: ProductionEnvironmentProfile) -> str:
    return canonical_hash(
        profile.model_dump(
            mode="json",
            include={
                "environment_id",
                "name",
                "engine",
                "environment_kind",
                "execution_mode",
                "read_only_preflight",
                "deployment_enabled",
                "environment_validation_run_id",
                "verification_source",
                "verified",
                "synthetic_profile",
            },
        )
    )


def artifact_failures(artifact_row, version, package) -> list[str]:
    """Re-derive every hash the package depends on. Any mismatch is fatal."""
    failures = []
    snapshot = artifact_row.snapshot
    try:
        artifact = DevelopmentArtifact.model_validate(snapshot["artifact"])
    except Exception:
        return ["artifact_document_unreadable"]
    if artifact_content_hash(artifact) != artifact_row.artifact_hash:
        failures.append("artifact_content_hash_mismatch")
    if canonical_hash(snapshot["metric_ir"]) != artifact_row.metric_ir_hash:
        failures.append("artifact_metric_ir_hash_mismatch")
    if artifact_row.artifact_hash != version.artifact_hash:
        failures.append("version_artifact_hash_mismatch")
    if artifact_row.metric_ir_hash != version.metric_ir_hash:
        failures.append("version_metric_ir_hash_mismatch")
    if canonical_hash(version.metric_ir) != version.metric_ir_hash:
        failures.append("metric_ir_hash_mismatch")
    if package is not None:
        if package.artifact_id != artifact_row.artifact_id:
            failures.append("package_artifact_id_mismatch")
        if package.artifact_hash != version.artifact_hash:
            failures.append("package_artifact_hash_mismatch")
        if package.metric_ir_hash != version.metric_ir_hash:
            failures.append("package_metric_ir_hash_mismatch")
        if canonical_hash(package.metric_ir) != package.metric_ir_hash:
            failures.append("package_metric_ir_hash_unverifiable")
        if hashlib.sha256(package.sql.encode("utf-8")).hexdigest() != package.sql_hash:
            failures.append("package_sql_hash_mismatch")
        if artifact.code != package.sql:
            failures.append("package_sql_not_from_governed_artifact")
    return failures


def build_package(
    *,
    registry,
    profile: ProductionEnvironmentProfile,
    release,
    version,
    review,
    report,
    fingerprint: ProductionEnvironmentFingerprint | None = None,
) -> DeploymentPackage:
    """Assemble the package from lineage only. The client contributes identifiers.

    ``fingerprint`` is the observed runtime fingerprint. It is ``None`` for a
    synthetic environment and required for a real one, which is what stops a
    package approved against cluster A from being executed on cluster B.
    """
    artifact_row = registry.session.get(DevelopmentArtifactRow, version.artifact_id)
    if artifact_row is None:
        raise PackageIntegrityError("release_artifact_not_found")
    failures = artifact_failures(artifact_row, version, None)
    if failures:
        raise PackageIntegrityError(sorted(set(failures))[0])
    sql = DevelopmentArtifact.model_validate(artifact_row.snapshot["artifact"]).code
    package = DeploymentPackage(
        metric_definition_id=version.metric_definition_id,
        metric_version_id=version.metric_version_id,
        metric_key=version.metric_key,
        version=version.version,
        metric_version_content_hash=version.content_hash,
        metric_ir=version.metric_ir,
        metric_ir_hash=version.metric_ir_hash,
        artifact_id=version.artifact_id,
        artifact_hash=version.artifact_hash,
        sql=sql,
        sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        release_id=release.release_id,
        release_review_id=review.release_review_id,
        release_validation_report_hash=canonical_hash(report),
        environment_id=profile.environment_id,
        environment_profile_hash=environment_profile_hash(profile),
        environment_fingerprint_hash=(
            None if fingerprint is None else fingerprint.fingerprint_hash
        ),
        synthetic_data=version.synthetic_data or profile.synthetic_profile,
        content_hash="0" * 64,
    )
    return package.model_copy(update={"content_hash": deployment_package_hash(package)})


def package_failures(registry, package: DeploymentPackage, profile, fingerprint=None) -> list[str]:
    """Full re-verification used by preflight and by every package read."""
    failures = []
    if deployment_package_hash(package) != package.content_hash:
        failures.append("package_hash_mismatch")
    if environment_profile_hash(profile) != package.environment_profile_hash:
        failures.append("environment_profile_changed")
    if package.environment_id != profile.environment_id:
        failures.append("environment_id_mismatch")
    if package.environment_fingerprint_hash is not None:
        if fingerprint is None:
            failures.append("environment_fingerprint_not_observed")
        elif fingerprint.fingerprint_hash != package.environment_fingerprint_hash:
            failures.append("environment_fingerprint_mismatch")
    if (
        package.metric_version_content_hash
        != registry.version_by_id(package.metric_version_id).content_hash
    ):
        failures.append("metric_version_changed")
    if canonical_hash(package.metric_ir) != package.metric_ir_hash:
        failures.append("package_metric_ir_hash_unverifiable")
    artifact_row = registry.session.get(DevelopmentArtifactRow, package.artifact_id)
    if artifact_row is None:
        failures.append("release_artifact_not_found")
        return sorted(set(failures))
    version = registry.version_by_id(package.metric_version_id)
    failures.extend(artifact_failures(artifact_row, version, package))
    return sorted(set(failures))

"""Phase 6 service layer: governed version registration, registry reads, release lifecycle.

Three human gates stay separate: SQL approval ("may this SQL be executed in test?"),
promotion review ("should this candidate become a version?") and release review
("may this version be activated?"). None of them perform a deployment.
"""

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from airi.approvals.artifacts import DevelopmentArtifact, artifact_content_hash, canonical_hash
from airi.approvals.persistence import ApprovalRecordRow, DevelopmentArtifactRow
from airi.approvals.service import ApprovalService, utcnow
from airi.core.exceptions import AIRIError
from airi.core.execution import ExecutionContext
from airi.execution.models import ExecutionRequest
from airi.execution.service import ExecutionService
from airi.metric_ir.models import MetricIR
from airi.refinement.service import RefinementService
from airi.refinement.transform import RefinementError
from airi.reflection.evidence import EvidenceExtractor, decode, get_row
from airi.registry.models import (
    RELEASE_TRANSITIONS,
    ActivationRequest,
    MetricDefinition,
    MetricRelease,
    MetricReleaseEvent,
    MetricVersion,
    MetricVersionDiff,
    ReleasePolicy,
    ReleaseReview,
    ReleaseValidationReport,
    RollbackReview,
    ShadowReference,
    StagingValidationResult,
    VersionChange,
    metric_version_content_hash,
)
from airi.registry.persistence import (
    MetricDefinitionRow,
    MetricReleaseEventRow,
    MetricReleaseRow,
    MetricVersionRow,
    ReleaseReviewRow,
    ReleaseValidationRow,
    RollbackReviewRow,
)
from airi.registry.release import ReleaseDiagnostics
from airi.registry.shadow import ShadowComparator, metric_values
from airi.registry.versioning import MetricFamilyKey, MetricVersionPlanner, semantic_diff
from airi.temporal.service import TemporalService
from airi.testing.persistence import MetricTestRunRow

logger = logging.getLogger("airi.registry")


class RegistryError(RefinementError):
    """All registry and release governance failures are 409 domain errors."""


class RegistryNotFound(RegistryError):
    status_code = 404


def version_key(value: str):
    return tuple(int(part) for part in value.split("."))


class RegistryService:
    def __init__(self, session, state):
        self.session, self.state = session, state

    # ------------------------------------------------------------------ lineage

    def promotion_lineage(self, promotion_review_id):
        """Only an approved promotion review may open the versioning gate."""
        temporal = TemporalService(self.session, self.state)
        review = temporal.review(promotion_review_id)
        if review.decision != "approved_for_versioning":
            raise RegistryError("metric_version_not_authorized")
        if canonical_hash(review.evidence) != review.evidence_hash:
            raise RegistryError("promotion_evidence_changed")
        report = temporal.get(review.temporal_validation_run_id)
        if canonical_hash(report) != review.evidence.source_hashes["temporal_report"]:
            raise RegistryError("promotion_evidence_changed")
        refinement = review.evidence.refinement
        if canonical_hash(refinement) != review.evidence.source_hashes["refinement_report"]:
            raise RegistryError("promotion_evidence_changed")
        candidate_experiment = refinement.get("candidate_experiment") or {}
        if not candidate_experiment.get("experiment_run_id"):
            raise RegistryError("metric_version_lineage_changed")
        extracted = EvidenceExtractor(self.session, self.state.skills).extract(
            candidate_experiment["experiment_run_id"]
        )
        if not isinstance(extracted.metric_ir, MetricIR):
            raise RegistryError("metric_version_not_supported")
        if (
            extracted.artifact_id != refinement["candidate"]["artifact_id"]
            or extracted.artifact_hash != refinement["candidate"]["content_hash"]
            or extracted.metric_ir_hash != refinement["definition"]["metric_ir_hash"]
            or canonical_hash(extracted.metric_ir) != extracted.metric_ir_hash
        ):
            raise RegistryError("metric_version_lineage_changed")
        return review, report, refinement, extracted

    def _provenance(self, review, report, refinement, extracted):
        context = refinement.get("experiment_context") or {}
        environment_id = context.get("environment_validation_run_id")
        return {
            "promotion_review_id": review.promotion_review_id,
            "promotion_evidence_hash": review.evidence_hash,
            "promotion_decision": review.decision,
            "temporal_validation_run_id": report.spec.temporal_validation_run_id,
            "temporal_report_hash": review.evidence.source_hashes["temporal_report"],
            "temporal_status": report.status,
            "temporal_promotion_eligibility": report.promotion_eligibility,
            "refinement_run_id": report.spec.source_refinement_run_id,
            "refinement_report_hash": review.evidence.source_hashes["refinement_report"],
            "candidate_artifact_id": extracted.artifact_id,
            "candidate_artifact_hash": extracted.artifact_hash,
            "metric_ir_hash": extracted.metric_ir_hash,
            "test_report_hash": canonical_hash(
                review.evidence.linked_evidence["candidate_test_report"]
            ),
            "experiment_report_hash": extracted.experiment_report_hash,
            "evaluation_policy": extracted.evaluation_policy.model_dump(mode="json"),
            "label_definition_id": extracted.label_definition_id,
            "dataset_snapshot_id": extracted.dataset_snapshot_id,
            "baseline_artifact_id": refinement["plan"]["baseline_artifact_id"],
            "baseline_experiment_run_id": refinement["plan"]["baseline_experiment_run_id"],
            "environment_validation_run_id": environment_id,
            "environment_hash": TemporalService(self.session, self.state).environment_hash(
                environment_id
            ),
            "execution_mode": context.get("execution_mode"),
            "synthetic_data": extracted.synthetic_data,
        }

    # ------------------------------------------------------------ version create

    def create_version(self, request):
        review, report, refinement, extracted = self.promotion_lineage(request.promotion_review_id)
        if self.session.scalar(
            select(MetricVersionRow).where(
                MetricVersionRow.source_promotion_review_id == review.promotion_review_id
            )
        ):
            raise RegistryError("metric_version_already_registered")
        metric_key = MetricFamilyKey().derive(extracted.metric_ir.name)
        definition_row = self.session.scalar(
            select(MetricDefinitionRow).where(MetricDefinitionRow.metric_key == metric_key)
        )
        if definition_row is None:
            definition = MetricDefinition(
                metric_key=metric_key,
                display_name=MetricFamilyKey().display_name(metric_key),
            )
            definition_row = MetricDefinitionRow(
                metric_definition_id=definition.metric_definition_id,
                metric_key=definition.metric_key,
                display_name=definition.display_name,
                scenario=definition.scenario,
                entity_type=definition.entity_type,
                member_metric_names=[],
                active_version_id=None,
                definition_json=definition.model_dump(mode="json"),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            self.session.add(definition_row)
        definition = decode(MetricDefinition, definition_row.definition_json)
        previous = max(
            self._versions_for(definition.metric_definition_id),
            key=lambda item: version_key(item.version),
            default=None,
        )
        plan = MetricVersionPlanner().plan(extracted.metric_ir, previous)
        version = MetricVersion(
            metric_definition_id=definition.metric_definition_id,
            metric_key=metric_key,
            version=plan.version,
            metric_name=extracted.metric_ir.name,
            display_name=extracted.metric_ir.display_name,
            metric_ir=extracted.metric_ir,
            metric_ir_hash=extracted.metric_ir_hash,
            artifact_id=extracted.artifact_id,
            artifact_hash=extracted.artifact_hash,
            source_refinement_run_id=report.spec.source_refinement_run_id,
            source_temporal_validation_run_id=report.spec.temporal_validation_run_id,
            source_promotion_review_id=review.promotion_review_id,
            version_change=VersionChange(
                kind=plan.kind,
                previous_version=previous.version if previous else None,
                changed_fields=plan.changed_fields,
            ),
            provenance=self._provenance(review, report, refinement, extracted),
            content_hash="0" * 64,
            synthetic_data=extracted.synthetic_data,
        )
        version = version.model_copy(update={"content_hash": metric_version_content_hash(version)})
        self.session.add(
            MetricVersionRow(
                metric_version_id=version.metric_version_id,
                metric_definition_id=definition.metric_definition_id,
                version=version.version,
                status=version.status,
                metric_name=version.metric_name,
                metric_ir_hash=version.metric_ir_hash,
                artifact_id=version.artifact_id,
                artifact_hash=version.artifact_hash,
                source_promotion_review_id=version.source_promotion_review_id,
                content_hash=version.content_hash,
                version_json=version.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        members = sorted({*definition.member_metric_names, version.metric_name})
        definition_row.member_metric_names = members
        definition_row.updated_at = utcnow()
        definition_row.definition_json = definition.model_copy(
            update={"member_metric_names": members, "updated_at": utcnow()}
        ).model_dump(mode="json")
        self._event(
            definition.metric_definition_id,
            "version_registered",
            actor="registry-service",
            metric_version_id=version.metric_version_id,
            metadata={
                "version": version.version,
                "version_change": version.version_change.kind,
                "synthetic_data": version.synthetic_data,
            },
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise RegistryError("metric_version_already_registered") from None
        logger.info(
            "metric_version_registered",
            extra={
                "metric_definition_id": definition.metric_definition_id,
                "metric_version_id": version.metric_version_id,
                "version": version.version,
                "status": version.status,
            },
        )
        return version

    # --------------------------------------------------------------- registry

    def definitions(self):
        rows = self.session.scalars(
            select(MetricDefinitionRow).order_by(MetricDefinitionRow.metric_key)
        )
        return [self._verify_definition(row) for row in rows]

    def definition(self, metric_key):
        row = self.session.scalar(
            select(MetricDefinitionRow).where(MetricDefinitionRow.metric_key == metric_key)
        )
        if row is None:
            raise RegistryNotFound("metric_definition_not_found")
        return self._verify_definition(row)

    def definition_by_id(self, metric_definition_id):
        return self._verify_definition(
            get_row(self.session, MetricDefinitionRow, metric_definition_id)
        )

    def _verify_definition(self, row):
        """The active pointer column and the stored document must never disagree."""
        definition = decode(MetricDefinition, row.definition_json)
        if (
            row.active_version_id != definition.active_version_id
            or row.metric_key != definition.metric_key
        ):
            raise RegistryError("metric_registry_integrity_error")
        return definition

    def _apply_active_version(self, metric_definition_id, version_id):
        row = self.session.get(MetricDefinitionRow, metric_definition_id, populate_existing=True)
        definition = decode(MetricDefinition, row.definition_json)
        row.definition_json = definition.model_copy(
            update={"active_version_id": version_id, "updated_at": datetime.now(UTC)}
        ).model_dump(mode="json")
        # The pointer column and the stored document must be durable together before
        # anything re-reads the row: `get_row` uses populate_existing=True, which
        # discards an unflushed attribute change and would silently revert this write.
        self.session.flush()

    def versions(self, metric_key):
        definition = self.definition(metric_key)
        return self._versions_for(definition.metric_definition_id)

    def _versions_for(self, metric_definition_id):
        rows = self.session.scalars(
            select(MetricVersionRow).where(
                MetricVersionRow.metric_definition_id == metric_definition_id
            )
        )
        versions = [
            self._verify_version(decode(MetricVersion, row.version_json), row) for row in rows
        ]
        return sorted(versions, key=lambda item: version_key(item.version))

    def version(self, metric_key, version):
        definition = self.definition(metric_key)
        row = self.session.scalar(
            select(MetricVersionRow).where(
                MetricVersionRow.metric_definition_id == definition.metric_definition_id,
                MetricVersionRow.version == version,
            )
        )
        if row is None:
            raise RegistryNotFound("metric_version_not_found")
        return self._verify_version(decode(MetricVersion, row.version_json), row)

    def version_by_id(self, metric_version_id):
        row = get_row(self.session, MetricVersionRow, metric_version_id)
        return self._verify_version(decode(MetricVersion, row.version_json), row)

    def active_version(self, metric_key):
        definition = self.definition(metric_key)
        if definition.active_version_id is None:
            return None
        return self.version_by_id(definition.active_version_id)

    def compare(self, metric_key, from_version, to_version):
        left, right = self.version(metric_key, from_version), self.version(metric_key, to_version)
        changed, unchanged = semantic_diff(left.metric_ir, right.metric_ir)
        classifier = MetricVersionPlanner().classifier
        return MetricVersionDiff(
            metric_key=metric_key,
            from_version=left.version,
            to_version=right.version,
            changed_fields=changed,
            unchanged=unchanged,
            classification=classifier.classify(changed),
        )

    def events(self, metric_key):
        definition = self.definition(metric_key)
        rows = self.session.scalars(
            select(MetricReleaseEventRow)
            .where(MetricReleaseEventRow.metric_definition_id == definition.metric_definition_id)
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

    def _verify_version(self, version, row):
        if (
            metric_version_content_hash(version) != row.content_hash
            or canonical_hash(version.metric_ir) != row.metric_ir_hash
            or canonical_hash(version.metric_ir) != version.metric_ir_hash
            or row.artifact_hash != version.artifact_hash
            or row.status != version.status
        ):
            raise RegistryError("metric_registry_integrity_error")
        return version

    # ------------------------------------------------------------------ release

    def release(self, release_id):
        row = get_row(self.session, MetricReleaseRow, release_id)
        release = decode(MetricRelease, row.release_json)
        if (
            row.status != release.status
            or row.version != release.version
            or row.target_environment != release.target_environment
            or row.release_policy_version != release.release_policy.version
            or row.metric_version_id != release.metric_version_id
        ):
            raise RegistryError("metric_registry_integrity_error")
        if (
            release.release_provenance.get("metric_version_content_hash")
            != self.version_by_id(release.metric_version_id).content_hash
        ):
            raise RegistryError("metric_registry_integrity_error")
        return release

    def validation_report(self, release_id):
        row = get_row(self.session, ReleaseValidationRow, release_id)
        report = decode(ReleaseValidationReport, row.report_json)
        if (
            canonical_hash(report) != row.report_hash
            or report.release_eligibility != row.release_eligibility
            or report.status != row.status
        ):
            raise RegistryError("release_evidence_changed")
        return report

    def release_review(self, release_review_id):
        row = get_row(self.session, ReleaseReviewRow, release_review_id)
        review = decode(ReleaseReview, row.review_json)
        if canonical_hash(review) != row.review_hash or review.decision != row.decision:
            raise RegistryError("release_evidence_changed")
        return review

    def rollback_review(self, rollback_review_id):
        row = get_row(self.session, RollbackReviewRow, rollback_review_id)
        review = decode(RollbackReview, row.review_json)
        if canonical_hash(review) != row.review_hash or review.decision != row.decision:
            raise RegistryError("release_evidence_changed")
        return review

    def _transition(self, release, target):
        if target not in RELEASE_TRANSITIONS[release.status]:
            raise RegistryError("release_state_invalid")
        return release.model_copy(update={"status": target})

    def _save_release(self, release):
        row = get_row(self.session, MetricReleaseRow, release.release_id)
        row.status = release.status
        row.release_json = release.model_dump(mode="json")
        row.validated_at = utcnow() if release.validated_at else None
        row.activated_at = utcnow() if release.activated_at else None

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

    def _set_version_status(self, metric_version_id, status):
        row = get_row(self.session, MetricVersionRow, metric_version_id)
        version = decode(MetricVersion, row.version_json).model_copy(update={"status": status})
        row.status, row.version_json = status, version.model_dump(mode="json")
        # Same reason as _apply_active_version: the status column and the stored
        # document are compared on every read, so they must be durable together.
        self.session.flush()

    def _production_verified(self):
        # No verified production environment exists in this repository.
        return self.state.settings.environment == "production"

    def create_release(self, request):
        version = self.version_by_id(request.metric_version_id)
        if request.target_environment == "production" and not self._production_verified():
            raise RegistryError("production_environment_not_verified")
        definition = self.definition_by_id(version.metric_definition_id)
        release = MetricRelease(
            metric_definition_id=definition.metric_definition_id,
            metric_version_id=version.metric_version_id,
            metric_key=definition.metric_key,
            version=version.version,
            target_environment=request.target_environment,
            expected_active_version_id=definition.active_version_id,
            release_policy=ReleasePolicy(),
            release_provenance={
                "metric_version_content_hash": version.content_hash,
                "metric_ir_hash": version.metric_ir_hash,
                "candidate_artifact_hash": version.artifact_hash,
                "promotion_review_id": version.source_promotion_review_id,
                "temporal_validation_run_id": version.source_temporal_validation_run_id,
                "refinement_run_id": version.source_refinement_run_id,
                "temporal_report_hash": version.provenance["temporal_report_hash"],
                "promotion_evidence_hash": version.provenance["promotion_evidence_hash"],
            },
        )
        release = self._transition(release, "pending_staging_validation")
        self.session.add(
            MetricReleaseRow(
                release_id=release.release_id,
                metric_definition_id=release.metric_definition_id,
                metric_version_id=release.metric_version_id,
                version=release.version,
                target_environment=release.target_environment,
                status=release.status,
                expected_active_version_id=release.expected_active_version_id,
                release_policy_version=release.release_policy.version,
                release_json=release.model_dump(mode="json"),
                created_at=utcnow(),
                validated_at=None,
                activated_at=None,
            )
        )
        if version.status == "registered":
            self._set_version_status(version.metric_version_id, "release_candidate")
        self._event(
            release.metric_definition_id,
            "release_created",
            actor="registry-service",
            metric_version_id=release.metric_version_id,
            release_id=release.release_id,
            metadata={
                "target_environment": release.target_environment,
                "logical_activation_only": release.release_policy.logical_activation_only,
            },
        )
        self.session.commit()
        return release

    # ------------------------------------------------------- release validation

    def _approved_approval(self, artifact_id):
        row = self.session.scalar(
            select(ApprovalRecordRow).where(
                ApprovalRecordRow.artifact_id == artifact_id,
                ApprovalRecordRow.decision == "approved",
            )
        )
        if row is None:
            raise RegistryError("release_artifact_not_approved")
        ApprovalService(self.session).require_approved(row.approval_id, artifact_id)
        return row.approval_id

    def _promotion_evidence(self, version):
        review = TemporalService(self.session, self.state).review(
            version.provenance["promotion_review_id"]
        )
        if canonical_hash(review.evidence) != review.evidence_hash:
            raise RegistryError("release_evidence_changed")
        return review.evidence

    def _evidence_checks(self, version):
        """Re-verify the whole research chain before a release may be validated."""
        profile = version.provenance
        try:
            temporal = TemporalService(self.session, self.state)
            review = temporal.review(profile["promotion_review_id"])
            if (
                canonical_hash(review.evidence) != review.evidence_hash
                or review.evidence_hash != profile["promotion_evidence_hash"]
                or review.decision != "approved_for_versioning"
            ):
                raise RegistryError("release_evidence_changed")
            report = temporal.get(profile["temporal_validation_run_id"])
            if canonical_hash(report) != profile["temporal_report_hash"]:
                raise RegistryError("release_evidence_changed")
            refinement = RefinementService(self.session, self.state).get(
                profile["refinement_run_id"]
            )
            if canonical_hash(refinement) != profile["refinement_report_hash"]:
                raise RegistryError("release_evidence_changed")
            artifact_row = get_row(self.session, DevelopmentArtifactRow, version.artifact_id)
            artifact = decode(DevelopmentArtifact, artifact_row.snapshot["artifact"])
            if (
                artifact_row.artifact_hash != version.artifact_hash
                or artifact_row.metric_ir_hash != version.metric_ir_hash
                or artifact_content_hash(artifact) != version.artifact_hash
                or canonical_hash(artifact_row.snapshot["metric_ir"]) != version.metric_ir_hash
            ):
                raise RegistryError("release_evidence_changed")
            candidate = review.evidence.refinement["candidate_experiment"]
            test_row = get_row(self.session, MetricTestRunRow, candidate["test_run_id"])
            if canonical_hash(test_row.report_json) != profile["test_report_hash"]:
                raise RegistryError("release_evidence_changed")
            extracted = EvidenceExtractor(self.session, self.state.skills).extract(
                candidate["experiment_run_id"]
            )
            if (
                extracted.experiment_report_hash != profile["experiment_report_hash"]
                or extracted.artifact_hash != version.artifact_hash
                or extracted.metric_ir_hash != version.metric_ir_hash
            ):
                raise RegistryError("release_evidence_changed")
        except RegistryError:
            raise
        except AIRIError as exc:
            raise RegistryError("release_evidence_changed") from exc
        return {
            "promotion_review": "verified",
            "promotion_evidence_hash": profile["promotion_evidence_hash"],
            "temporal_validation_run_id": profile["temporal_validation_run_id"],
            "temporal_report_hash": profile["temporal_report_hash"],
            "refinement_report_hash": profile["refinement_report_hash"],
            "candidate_artifact_hash": version.artifact_hash,
            "metric_ir_hash": version.metric_ir_hash,
            "test_report_hash": profile["test_report_hash"],
            "experiment_report_hash": profile["experiment_report_hash"],
            "metric_version_content_hash": version.content_hash,
        }

    def _execute(self, artifact_id, approval_id, request_id):
        executed, _, _ = ExecutionService(self.session, self.state.query_executor).run(
            ExecutionRequest(artifact_id=artifact_id, approval_id=approval_id, max_rows=1000),
            request_id,
        )
        return executed

    def _artifact_anchor(self, artifact_id):
        return ExecutionContext.model_validate(
            get_row(self.session, DevelopmentArtifactRow, artifact_id).snapshot["execution_context"]
        ).anchor_time

    def _staging_result(self, release, version, executed):
        values, _ = metric_values(executed.rows, version.metric_name)
        columns = {column.name for column in executed.columns}
        schema_ok = {"entity_id", version.metric_name}.issubset(columns) and len(columns) <= 16
        non_null = sum(value is not None for value in values.values())
        coverage = (non_null / len(values)) if values else None
        null_rate = ((len(values) - non_null) / len(values)) if values else None
        warnings = [warning for warning in executed.warnings if warning]
        if not values:
            warnings.append("staging_empty_result")
        if executed.status != "success":
            warnings.append(f"staging_failure:{executed.failure_category}")
        status = (
            "failed"
            if executed.status != "success" or not schema_ok or executed.truncated
            else "inconclusive"
            if not values
            else "passed"
        )
        return StagingValidationResult(
            status=status,
            execution_mode=self.state.settings.effective_execution_mode,
            target_environment=release.target_environment,
            artifact_id=version.artifact_id,
            artifact_hash=version.artifact_hash,
            metric_ir_hash=version.metric_ir_hash,
            execution_run_id=executed.execution_run_id,
            failure_category=executed.failure_category,
            row_count=executed.row_count,
            truncated=executed.truncated,
            schema_ok=schema_ok,
            coverage=coverage,
            null_rate=null_rate,
            non_null_count=non_null,
            warnings=warnings,
        )

    def _shadow_reference(self, version, definition, refinement):
        if (
            definition.active_version_id is not None
            and definition.active_version_id != version.metric_version_id
        ):
            active = self.version_by_id(definition.active_version_id)
            return ShadowReference(
                role="active_metric_version",
                label=active.version,
                artifact_id=active.artifact_id,
                artifact_hash=active.artifact_hash,
                metric_name=active.metric_name,
                metric_version_id=active.metric_version_id,
                version=active.version,
            )
        baseline_row = get_row(
            self.session, DevelopmentArtifactRow, refinement["plan"]["baseline_artifact_id"]
        )
        return ShadowReference(
            role="lineage_baseline_artifact",
            label=baseline_row.metric_name,
            artifact_id=baseline_row.artifact_id,
            artifact_hash=baseline_row.artifact_hash,
            metric_name=baseline_row.metric_name,
        )

    def _shadow(self, release, version, definition, refinement, request_id):
        baseline = self._shadow_reference(version, definition, refinement)
        candidate = ShadowReference(
            role="candidate_metric_version",
            label=version.version,
            artifact_id=version.artifact_id,
            artifact_hash=version.artifact_hash,
            metric_name=version.metric_name,
            metric_version_id=version.metric_version_id,
            version=version.version,
        )
        comparator = ShadowComparator()
        candidate_anchor = self._artifact_anchor(version.artifact_id)
        if self._artifact_anchor(baseline.artifact_id) != candidate_anchor:
            raise RegistryError("shadow_anchor_mismatch")
        baseline_executed = self._execute(
            baseline.artifact_id, self._approved_approval(baseline.artifact_id), request_id
        )
        candidate_executed = self._execute(
            version.artifact_id, self._approved_approval(version.artifact_id), request_id
        )
        if baseline_executed.status != "success" or candidate_executed.status != "success":
            return comparator.failed("execution_failed", baseline=baseline, candidate=candidate)
        return comparator.compare(
            baseline=baseline,
            candidate=candidate,
            baseline_rows=baseline_executed.rows,
            candidate_rows=candidate_executed.rows,
            dataset_snapshot_id=refinement["experiment_context"]["dataset_snapshot_id"],
            anchor_time=candidate_anchor,
            policy=release.release_policy,
        )

    def validate(self, release_id, request_id):
        release = self.release(release_id)
        if release.status != "pending_staging_validation":
            raise RegistryError("release_not_validatable")
        version = self.version_by_id(release.metric_version_id)
        definition = self.definition_by_id(version.metric_definition_id)
        checks = self._evidence_checks(version)
        refinement = self._promotion_evidence(version).refinement
        executed = self._execute(
            version.artifact_id, self._approved_approval(version.artifact_id), request_id
        )
        staging = self._staging_result(release, version, executed)
        shadow = (
            None
            if staging.status == "failed"
            else self._shadow(release, version, definition, refinement, request_id)
        )
        findings, eligibility, status, warnings, missing = ReleaseDiagnostics().assess(
            staging, shadow, release.release_policy, synthetic_data=version.synthetic_data
        )
        report = ReleaseValidationReport(
            release_id=release.release_id,
            metric_version_id=version.metric_version_id,
            metric_key=release.metric_key,
            version=version.version,
            status=status,
            release_eligibility=eligibility,
            staging=staging,
            shadow=shadow,
            evidence_checks=checks,
            findings=findings,
            warnings=warnings,
            missing_evidence=missing,
            synthetic_data=version.synthetic_data,
            execution_mode=self.state.settings.effective_execution_mode,
            release_policy_version=release.release_policy.version,
        )
        self.session.add(
            ReleaseValidationRow(
                release_id=release.release_id,
                status=report.status,
                release_eligibility=report.release_eligibility,
                report_json=report.model_dump(mode="json"),
                report_hash=canonical_hash(report),
                created_at=utcnow(),
            )
        )
        release = release.model_copy(
            update={
                "validated_at": datetime.now(UTC),
                "validation_report_hash": canonical_hash(report),
            }
        )
        release = self._transition(release, "failed" if status == "failed" else "staging_validated")
        self._save_release(release)
        self._event(
            release.metric_definition_id,
            "staging_validated",
            actor="registry-service",
            metric_version_id=version.metric_version_id,
            release_id=release.release_id,
            metadata={
                "status": report.status,
                "release_eligibility": report.release_eligibility,
                "execution_mode": report.execution_mode,
            },
        )
        self.session.commit()
        logger.info(
            "release_validated",
            extra={
                "metric_version_id": version.metric_version_id,
                "release_id": release.release_id,
                "status": report.status,
                "release_eligibility": report.release_eligibility,
            },
        )
        return report

    # ----------------------------------------------------------- release review

    def create_review(self, release_id):
        release = self.release(release_id)
        # State is checked before evidence: an unvalidated release is a transition
        # conflict, not a missing resource.
        existing = self.session.scalar(
            select(ReleaseReviewRow).where(ReleaseReviewRow.release_id == release_id)
        )
        if release.status == "pending_release_review":
            previous = decode(ReleaseReview, existing.review_json) if existing else None
            if previous is None or previous.decision != "need_more_evidence":
                raise RegistryError("release_review_already_exists")
            review = ReleaseReview(
                release_review_id=previous.release_review_id,
                release_id=previous.release_id,
                metric_version_id=previous.metric_version_id,
                validation_report_hash=release.validation_report_hash,
                previous_decisions=[
                    *previous.previous_decisions,
                    {
                        "decision": previous.decision,
                        "reviewer": previous.reviewer,
                        "comment": previous.comment,
                    },
                ],
            )
            existing.decision = "pending"
            existing.reviewer, existing.comment, existing.reviewed_at = None, None, None
            existing.review_json, existing.review_hash = (
                review.model_dump(mode="json"),
                canonical_hash(review),
            )
            self.session.commit()
            return review
        if release.status != "staging_validated":
            raise RegistryError("release_not_ready_for_review")
        report = self.validation_report(release_id)
        if report.release_eligibility != "eligible_for_release_review":
            raise RegistryError("release_not_eligible")
        review = ReleaseReview(
            release_id=release.release_id,
            metric_version_id=release.metric_version_id,
            validation_report_hash=release.validation_report_hash,
        )
        self.session.add(
            ReleaseReviewRow(
                release_review_id=review.release_review_id,
                release_id=review.release_id,
                metric_version_id=review.metric_version_id,
                decision="pending",
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
            )
        )
        release = self._transition(release, "pending_release_review")
        self._save_release(release)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise RegistryError("release_review_already_exists") from None
        return review

    def decide_review(self, review_id, payload, request_id):
        review = self.release_review(review_id)
        if review.decision != "pending":
            raise RegistryError("release_review_already_decided")
        release = self.release(review.release_id)
        report = self.validation_report(release.release_id)
        if (
            release.validation_report_hash != review.validation_report_hash
            or canonical_hash(report) != review.validation_report_hash
        ):
            raise RegistryError("release_evidence_changed")
        self._evidence_checks(self.version_by_id(release.metric_version_id))
        updated = review.model_copy(
            update={**payload.model_dump(), "reviewed_at": datetime.now(UTC)}
        )
        changed = self.session.execute(
            update(ReleaseReviewRow)
            .where(
                ReleaseReviewRow.release_review_id == review_id,
                ReleaseReviewRow.decision == "pending",
            )
            .values(
                decision=payload.decision,
                reviewer=payload.reviewer,
                comment=payload.comment,
                reviewed_at=utcnow(),
                review_json=updated.model_dump(mode="json"),
                review_hash=canonical_hash(updated),
            )
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise RegistryError("release_review_already_decided")
        target = {"approved": "approved", "rejected": "failed"}.get(payload.decision)
        if target is not None:
            release = self._transition(release, target)
        self._save_release(release)
        self._event(
            release.metric_definition_id,
            "release_approved" if payload.decision == "approved" else "release_rejected",
            actor=payload.reviewer,
            metric_version_id=release.metric_version_id,
            release_id=release.release_id,
            metadata={"decision": payload.decision},
        )
        self.session.commit()
        logger.info(
            "release_review_decided",
            extra={
                "release_id": release.release_id,
                "release_review_id": review_id,
                "status": payload.decision,
                "request_id": request_id,
            },
        )
        return updated

    # -------------------------------------------------------------- activation

    def activate(self, release_id, payload: ActivationRequest, request_id):
        release = self.release(release_id)
        definition = self.definition_by_id(release.metric_definition_id)
        if release.status == "active" and definition.active_version_id == release.metric_version_id:
            return release
        if release.status != "approved":
            raise RegistryError("release_not_approved")
        condition = (
            MetricDefinitionRow.active_version_id.is_(None)
            if payload.expected_active_version is None
            else MetricDefinitionRow.active_version_id
            == self.version(release.metric_key, payload.expected_active_version).metric_version_id
        )
        changed = self.session.execute(
            update(MetricDefinitionRow)
            .where(
                MetricDefinitionRow.metric_definition_id == release.metric_definition_id,
                condition,
            )
            .values(active_version_id=release.metric_version_id, updated_at=utcnow())
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise RegistryError("activation_conflict")
        self._apply_active_version(release.metric_definition_id, release.metric_version_id)
        if release.expected_active_version_id is not None:
            self._set_version_status(release.expected_active_version_id, "retired")
        self._set_version_status(release.metric_version_id, "active")
        release = self._transition(release, "active").model_copy(
            update={"activated_at": datetime.now(UTC)}
        )
        self._save_release(release)
        self._event(
            release.metric_definition_id,
            "activated",
            actor="registry-service",
            metric_version_id=release.metric_version_id,
            release_id=release.release_id,
            metadata={
                "version": release.version,
                "previous_active_version_id": release.expected_active_version_id,
                "logical_activation_only": True,
            },
        )
        self.session.commit()
        logger.info(
            "release_activated",
            extra={
                "metric_definition_id": release.metric_definition_id,
                "metric_version_id": release.metric_version_id,
                "release_id": release.release_id,
                "version": release.version,
                "request_id": request_id,
            },
        )
        return release

    # ---------------------------------------------------------------- rollback

    def request_rollback(self, metric_key, payload):
        definition = self.definition(metric_key)
        target = self.version(metric_key, payload.target_version)
        if definition.active_version_id is None:
            raise RegistryError("rollback_no_active_version")
        active = self.version_by_id(definition.active_version_id)
        if active.metric_version_id == target.metric_version_id:
            raise RegistryError("rollback_target_already_active")
        review = RollbackReview(
            metric_definition_id=definition.metric_definition_id,
            metric_key=metric_key,
            from_version_id=active.metric_version_id,
            from_version=active.version,
            to_version_id=target.metric_version_id,
            to_version=target.version,
            reason=payload.reason,
        )
        self.session.add(
            RollbackReviewRow(
                rollback_review_id=review.rollback_review_id,
                metric_definition_id=review.metric_definition_id,
                from_version_id=review.from_version_id,
                to_version_id=review.to_version_id,
                decision="pending",
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
            )
        )
        self._event(
            definition.metric_definition_id,
            "rollback_requested",
            actor="registry-service",
            metric_version_id=target.metric_version_id,
            metadata={"from_version": active.version, "to_version": target.version},
        )
        self.session.commit()
        return review

    def reconcile_active_version(
        self, metric_definition_id, *, from_version_id, to_version_id, actor, reason
    ):
        """The single implementation of a reviewed pointer move.

        Phase 6 registry rollback and Phase 7 production reconciliation both call
        this, so the column/document discipline exists in exactly one place. The
        caller owns the commit, which is what lets Phase 7 treat a failure here as
        a named partial-failure state instead of a silent success.
        """
        active_move = self.session.execute(
            update(MetricDefinitionRow)
            .where(
                MetricDefinitionRow.metric_definition_id == metric_definition_id,
                MetricDefinitionRow.active_version_id == from_version_id,
            )
            .values(active_version_id=to_version_id, updated_at=utcnow())
        )
        if active_move.rowcount != 1:
            self.session.rollback()
            raise RegistryError("rollback_conflict")
        self._apply_active_version(metric_definition_id, to_version_id)
        # History is preserved: the replaced version is retired, never deleted.
        self._set_version_status(from_version_id, "retired")
        self._set_version_status(to_version_id, "active")
        # The release row column and its stored document are written together; a bare
        # column update would silently desynchronise them and trip the integrity guard.
        for release_row in self.session.scalars(
            select(MetricReleaseRow).where(
                MetricReleaseRow.metric_version_id == from_version_id,
                MetricReleaseRow.status == "active",
            )
        ):
            release = decode(MetricRelease, release_row.release_json)
            self._save_release(self._transition(release, "rolled_back"))
        self.session.flush()
        metadata = {"from_version_id": from_version_id, "to_version_id": to_version_id}
        self._event(
            metric_definition_id,
            "rollback_approved",
            actor=actor,
            metric_version_id=to_version_id,
            metadata={**metadata, "reason": reason},
        )
        self._event(
            metric_definition_id,
            "rolled_back",
            actor="registry-service",
            metric_version_id=to_version_id,
            metadata=metadata,
        )
        return self.definition_by_id(metric_definition_id)

    def decide_rollback(self, review_id, payload, request_id):
        review = self.rollback_review(review_id)
        if review.decision != "pending":
            raise RegistryError("rollback_already_decided")
        updated = review.model_copy(
            update={**payload.model_dump(), "reviewed_at": datetime.now(UTC)}
        )
        changed = self.session.execute(
            update(RollbackReviewRow)
            .where(
                RollbackReviewRow.rollback_review_id == review_id,
                RollbackReviewRow.decision == "pending",
            )
            .values(
                decision=payload.decision,
                reviewer=payload.reviewer,
                comment=payload.comment,
                reviewed_at=utcnow(),
                review_json=updated.model_dump(mode="json"),
                review_hash=canonical_hash(updated),
            )
        )
        if changed.rowcount != 1:
            self.session.rollback()
            raise RegistryError("rollback_already_decided")
        if payload.decision == "rejected":
            self._event(
                review.metric_definition_id,
                "rollback_rejected",
                actor=payload.reviewer,
                metric_version_id=review.to_version_id,
                metadata={"from_version": review.from_version, "to_version": review.to_version},
            )
            self.session.commit()
            return updated
        self.reconcile_active_version(
            review.metric_definition_id,
            from_version_id=review.from_version_id,
            to_version_id=review.to_version_id,
            actor=payload.reviewer,
            reason=review.reason,
        )
        self.session.commit()
        logger.info(
            "metric_rolled_back",
            extra={
                "metric_definition_id": review.metric_definition_id,
                "metric_version_id": review.to_version_id,
                "rollback_review_id": review_id,
                "request_id": request_id,
            },
        )
        return updated

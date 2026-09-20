import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from airi.approvals.artifacts import canonical_hash
from airi.approvals.service import ApprovalService, utcnow
from airi.core.execution import ExecutionContext
from airi.environments.persistence import EnvironmentValidationRow
from airi.experiments.models import (
    DatasetSnapshot,
    ExperimentReport,
    ExperimentSpec,
    LabelDefinition,
)
from airi.experiments.persistence import (
    DatasetSnapshotRow,
    ExperimentEvaluationRow,
    ExperimentSpecRow,
    LabelDefinitionRow,
)
from airi.metric_ir.models import MetricIR
from airi.refinement.persistence import RefinementRunRow
from airi.refinement.service import RefinementService
from airi.refinement.transform import RefinementError
from airi.reflection.evidence import EvidenceExtractor, decode, get_row
from airi.temporal.models import (
    PromotionEvidencePackage,
    PromotionReview,
    SliceArtifact,
    TemporalDatasetSeries,
    TemporalValidationReport,
    TemporalValidationSpec,
)
from airi.temporal.persistence import (
    PromotionReviewRow,
    TemporalResultRow,
    TemporalRunRow,
    TemporalSeriesRow,
)
from airi.testing.persistence import MetricTestRunRow


class TemporalError(RefinementError):
    pass


class TemporalService:
    def __init__(self, session, state):
        self.session, self.state = session, state

    def environment_hash(self, validation_id):
        config = self.state.settings
        fields = (
            "spark_host",
            "spark_port",
            "spark_username",
            "spark_auth_mode",
            "spark_database",
            "spark_session_timezone",
            "spark_fixture_mapping",
            "spark_read_only_attested",
            "spark_test_host",
            "spark_test_port",
        )
        return canonical_hash(
            {
                "mode": config.effective_execution_mode,
                "configuration": {key: getattr(config, key) for key in fields},
                "validation": get_row(
                    self.session, EnvironmentValidationRow, validation_id
                ).report_json
                if validation_id
                else None,
            }
        )

    def register_series(self, payload):
        label = decode(
            LabelDefinition,
            get_row(self.session, LabelDefinitionRow, payload.label_definition_id).definition_json,
        )
        hashes = {}
        for item in payload.slices:
            snapshot = decode(
                DatasetSnapshot,
                get_row(self.session, DatasetSnapshotRow, item.dataset_snapshot_id).snapshot_json,
            )
            if (
                snapshot.snapshot_time != item.observation_time
                or snapshot.entity_key != label.entity_key
            ):
                raise TemporalError("temporal_series_invalid")
            hashes[item.dataset_snapshot_id] = canonical_hash(snapshot)
        series = TemporalDatasetSeries(
            **payload.model_dump(), snapshot_hashes=hashes, label_hash=canonical_hash(label)
        )
        self.session.add(
            TemporalSeriesRow(
                series_id=series.series_id,
                name=series.name,
                reference_slice_id=series.reference_slice_id,
                oot_slice_id=next(s.time_slice_id for s in series.slices if s.role == "oot"),
                series_json=series.model_dump(mode="json"),
                content_hash=canonical_hash(series),
                created_at=utcnow(),
            )
        )
        self.session.commit()
        return series

    def series(self, series_id):
        row = get_row(self.session, TemporalSeriesRow, series_id)
        series = decode(TemporalDatasetSeries, row.series_json)
        if canonical_hash(series) != row.content_hash:
            raise TemporalError("temporal_series_invalid")
        if (
            canonical_hash(
                get_row(
                    self.session, LabelDefinitionRow, series.label_definition_id
                ).definition_json
            )
            != series.label_hash
        ):
            raise TemporalError("temporal_series_invalid")
        for item in series.slices:
            snapshot = get_row(self.session, DatasetSnapshotRow, item.dataset_snapshot_id)
            if (
                canonical_hash(snapshot.snapshot_json)
                != series.snapshot_hashes[item.dataset_snapshot_id]
            ):
                raise TemporalError("temporal_series_invalid")
        return series

    def lineage(self, refinement_id, series):
        service = RefinementService(self.session, self.state)
        report = service.get(refinement_id)
        row = get_row(self.session, RefinementRunRow, refinement_id)
        if (
            report.run.status != "completed"
            or row.status != "completed"
            or not row.final_decision_recorded
            or not report.final_decision
            or report.final_decision.decision != "accept_candidate_for_further_validation"
        ):
            raise TemporalError("candidate_not_authorized_for_temporal_validation")
        view, baseline, reflection_hash, reflection = service.inputs(report.plan.request)
        if (
            service.input_hash(
                report.plan.request, view, baseline, reflection_hash, report.plan.comparison_policy
            )
            != report.plan.refinement_input_hash
        ):
            raise TemporalError("temporal_lineage_changed")
        if (
            not report.candidate_experiment
            or row.candidate_artifact_id != report.candidate.artifact_id
            or row.baseline_artifact_id != baseline.artifact_id
        ):
            raise TemporalError("temporal_lineage_changed")
        candidate = EvidenceExtractor(self.session, self.state.skills).extract(
            report.candidate_experiment.experiment_run_id
        )
        if (
            candidate.artifact_id != report.candidate.artifact_id
            or candidate.artifact_hash != report.candidate.content_hash
            or canonical_hash(candidate.metric_ir) != report.definition.metric_ir_hash
        ):
            raise TemporalError("temporal_lineage_changed")
        original = decode(
            ExperimentReport,
            get_row(self.session, ExperimentEvaluationRow, baseline.experiment_run_id).report_json,
        )
        spec = decode(
            ExperimentSpec,
            get_row(self.session, ExperimentSpecRow, original.run.experiment_spec_id).spec_json,
        )
        if (
            series.label_definition_id != spec.label_definition_id
            or self.state.settings.effective_execution_mode != original.execution_mode
        ):
            raise TemporalError("temporal_series_invalid")
        oot = next(s for s in series.slices if s.role == "oot")
        if (
            oot.dataset_snapshot_id
            in {e.dataset_snapshot_id for e in reflection.evidence}
            | {candidate.dataset_snapshot_id}
            or oot.observation_time <= spec.observation_time
        ):
            raise TemporalError(
                "temporal_validation_invalid: OOT must be unused "
                "and later than selection observation"
            )
        # Recheck original SQL approvals; subsequent anchors receive separate approvals.
        ApprovalService(self.session).require_approved(spec.approval_id, baseline.artifact_id)
        ApprovalService(self.session).require_approved(
            report.candidate_experiment.approval_id, candidate.artifact_id
        )
        return report, spec, baseline, candidate

    def create(self, request):
        series = self.series(request.series_id)
        report, original, baseline, candidate = self.lineage(
            request.source_refinement_run_id, series
        )
        if self.session.scalar(
            select(TemporalRunRow).where(
                TemporalRunRow.source_refinement_run_id == request.source_refinement_run_id
            )
        ):
            raise TemporalError("temporal_plan_already_frozen")
        artifacts = []
        for item in sorted(series.slices, key=lambda s: s.observation_time):
            for role, evidence in (("baseline", baseline), ("candidate", candidate)):
                if not isinstance(evidence.metric_ir, MetricIR):
                    raise TemporalError("temporal_metric_not_supported")
                result = self.state.development_workflow.run_structured(
                    evidence.metric_ir,
                    ExecutionContext(anchor_time=item.observation_time),
                    candidate=role == "candidate",
                )
                ApprovalService(self.session).save_draft(result)
                artifacts.append(
                    SliceArtifact(
                        time_slice_id=item.time_slice_id,
                        role=role,
                        artifact_id=result.artifact.artifact_id,
                        content_hash=result.artifact.content_hash,
                        metric_ir_hash=canonical_hash(evidence.metric_ir),
                    )
                )
        provenance = {
            "refinement_report_hash": canonical_hash(report),
            "series_hash": canonical_hash(series),
            "baseline_artifact_hash": baseline.artifact_hash,
            "candidate_artifact_hash": candidate.artifact_hash,
            "proposal_id": report.run.proposal_id,
            "semantic_diff": report.definition.semantic_diff.model_dump(mode="json"),
            "snapshot_hashes": series.snapshot_hashes,
            "label_definition_id": series.label_definition_id,
            "label_hash": series.label_hash,
            "environment_validation_run_id": original.environment_validation_run_id,
            "environment_hash": self.environment_hash(original.environment_validation_run_id),
            "execution_mode": self.state.settings.effective_execution_mode,
            "baseline_experiment_report_hash": baseline.experiment_report_hash,
            "candidate_experiment_report_hash": candidate.experiment_report_hash,
        }
        spec = TemporalValidationSpec(
            **request.model_dump(),
            baseline_artifact_id=baseline.artifact_id,
            candidate_artifact_id=candidate.artifact_id,
            historical_slice_ids=[s.time_slice_id for s in series.slices if s.role == "historical"],
            oot_slice_id=next(s.time_slice_id for s in series.slices if s.role == "oot"),
            reference_slice_id=series.reference_slice_id,
            evaluation_policy=original.evaluation,
            slice_artifacts=artifacts,
            input_hash=canonical_hash(provenance),
            provenance=provenance,
        )
        result = TemporalValidationReport(
            spec=spec,
            status="pending_sql_review",
            synthetic_data=baseline.synthetic_data or candidate.synthetic_data,
            warnings=["Each anchor-specific SQL draft requires separate human SQL approval"],
            missing_evidence=["no production traffic", "no authenticated reviewer"],
        )
        self.session.add(
            TemporalRunRow(
                temporal_validation_run_id=spec.temporal_validation_run_id,
                source_refinement_run_id=request.source_refinement_run_id,
                baseline_artifact_id=baseline.artifact_id,
                candidate_artifact_id=candidate.artifact_id,
                series_id=series.series_id,
                status=result.status,
                stability_policy_version=spec.stability_policy.version,
                promotion_policy_version=spec.promotion_policy.version,
                spec_hash=canonical_hash(spec),
                spec_json=spec.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        self.session.add(
            TemporalResultRow(
                temporal_validation_run_id=spec.temporal_validation_run_id,
                status=result.status,
                promotion_eligibility=result.promotion_eligibility,
                report_json=result.model_dump(mode="json"),
                report_hash=canonical_hash(result),
                created_at=utcnow(),
            )
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise TemporalError("temporal_plan_already_frozen") from None
        return result

    def get(self, run_id):
        run = get_row(self.session, TemporalRunRow, run_id)
        row = get_row(self.session, TemporalResultRow, run_id)
        result = decode(TemporalValidationReport, row.report_json)
        if (
            canonical_hash(result) != row.report_hash
            or canonical_hash(result.spec) != run.spec_hash
            or result.spec.model_dump(mode="json") != run.spec_json
            or result.status != run.status
        ):
            raise TemporalError("temporal_evidence_changed")
        return result

    def save(self, result):
        run = get_row(self.session, TemporalRunRow, result.spec.temporal_validation_run_id)
        row = get_row(self.session, TemporalResultRow, run.temporal_validation_run_id)
        run.status = row.status = result.status
        run.finished_at = utcnow() if result.finished_at else None
        row.report_json, row.report_hash = result.model_dump(mode="json"), canonical_hash(result)
        row.promotion_eligibility = result.promotion_eligibility
        self.session.commit()

    def run(self, run_id, request_id):
        from airi.workflows.temporal_validation.workflow import run_temporal

        return run_temporal(self, run_id, request_id)

    def review(self, review_id):
        row = get_row(self.session, PromotionReviewRow, review_id)
        review = decode(PromotionReview, row.review_json)
        if (
            canonical_hash(review) != row.review_hash
            or canonical_hash(review.evidence) != review.evidence_hash
        ):
            raise TemporalError("promotion_evidence_changed")
        return review

    def create_review(self, request):
        report = self.get(request.temporal_validation_run_id)
        if report.promotion_eligibility != "eligible_for_review" or report.status not in (
            "passed",
            "passed_with_warnings",
        ):
            raise TemporalError("promotion_not_eligible")
        series = self.series(report.spec.series_id)
        refinement, _, _, _ = self.lineage(report.spec.source_refinement_run_id, series)
        if canonical_hash(refinement) != report.spec.provenance["refinement_report_hash"]:
            raise TemporalError("temporal_lineage_changed")
        self.verify_experiments(report)
        linked = {}
        for name, experiment_id in (
            ("original_experiment", refinement.run.baseline_experiment_run_id),
            ("candidate_experiment", refinement.run.candidate_experiment_run_id),
        ):
            linked[name] = get_row(self.session, ExperimentEvaluationRow, experiment_id).report_json
        linked["candidate_approval"] = (
            ApprovalService(self.session)
            .require_approved(
                refinement.candidate_experiment.approval_id, refinement.candidate.artifact_id
            )
            .model_dump(mode="json")
        )
        linked["candidate_test_report"] = get_row(
            self.session, MetricTestRunRow, refinement.candidate_experiment.test_run_id
        ).report_json
        linked["proposal"] = (
            RefinementService(self.session, self.state)
            .proposal(refinement.run.reflection_run_id, refinement.run.proposal_id)
            .proposal.model_dump(mode="json")
        )
        evidence = PromotionEvidencePackage(
            refinement=refinement.model_dump(mode="json"),
            linked_evidence=linked,
            temporal_validation=report,
            source_hashes={
                "temporal_report": canonical_hash(report),
                "refinement_report": canonical_hash(refinement),
            },
            missing_evidence=report.missing_evidence,
        )
        review = PromotionReview(
            temporal_validation_run_id=request.temporal_validation_run_id,
            candidate_artifact_id=report.spec.candidate_artifact_id,
            evidence=evidence,
            evidence_hash=canonical_hash(evidence),
        )
        self.session.add(
            PromotionReviewRow(
                promotion_review_id=review.promotion_review_id,
                temporal_validation_run_id=review.temporal_validation_run_id,
                candidate_artifact_id=review.candidate_artifact_id,
                decision="pending",
                review_json=review.model_dump(mode="json"),
                review_hash=canonical_hash(review),
                created_at=utcnow(),
            )
        )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise TemporalError("promotion_review_already_exists") from None
        return review

    def verify_experiments(self, report):
        for item in [*report.historical, *([report.oot] if report.oot else [])]:
            for value in (item.baseline, item.candidate):
                evidence = EvidenceExtractor(self.session, self.state.skills).extract(
                    value.experiment_run_id
                )
                if evidence.experiment_report_hash != value.experiment_report_hash:
                    raise TemporalError("temporal_evidence_changed")
                ApprovalService(self.session).require_approved(value.approval_id, value.artifact_id)

    def decide(self, review_id, payload, request_id):
        review = self.review(review_id)
        current = self.get(review.temporal_validation_run_id)
        if canonical_hash(current) != review.evidence.source_hashes["temporal_report"]:
            raise TemporalError("promotion_evidence_changed")
        self.verify_experiments(current)
        series = self.series(current.spec.series_id)
        refinement, _, _, _ = self.lineage(current.spec.source_refinement_run_id, series)
        if canonical_hash(refinement) != review.evidence.source_hashes["refinement_report"]:
            raise TemporalError("promotion_evidence_changed")
        updated = review.model_copy(
            update={**payload.model_dump(), "reviewed_at": datetime.now(UTC)}
        )
        change = self.session.execute(
            update(PromotionReviewRow)
            .where(
                PromotionReviewRow.promotion_review_id == review_id,
                PromotionReviewRow.decision == "pending",
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
        if change.rowcount != 1:
            self.session.rollback()
            raise TemporalError("promotion_already_decided")
        self.session.commit()
        logging.getLogger("airi.temporal").info(
            "promotion_review_decided",
            extra={
                "request_id": request_id,
                "promotion_review_id": review_id,
                "status": payload.decision,
            },
        )
        return updated

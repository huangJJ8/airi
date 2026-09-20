import logging
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select, update

from airi.approvals.artifacts import canonical_hash
from airi.approvals.persistence import ApprovalRecordRow, DevelopmentArtifactRow
from airi.approvals.service import ApprovalService, utcnow
from airi.execution.models import ExecutionRequest
from airi.experiments.models import ExperimentSpec
from airi.experiments.service import ExperimentService
from airi.refinement.comparison import BaselineCandidateComparison
from airi.reflection.evidence import EvidenceExtractor, decode, get_row
from airi.temporal.diagnostics import TemporalDiagnostics
from airi.temporal.models import OOTValidationResult, TemporalSliceResult
from airi.temporal.persistence import TemporalRunRow
from airi.temporal.service import TemporalError
from airi.temporal.statistics import fixed_boundaries, frozen_threshold, psi
from airi.workflows.testing.workflow import TestingWorkflow


def run_temporal(service, run_id, request_id):
    report = service.get(run_id)
    spec = report.spec
    if (
        service.environment_hash(spec.provenance["environment_validation_run_id"])
        != spec.provenance["environment_hash"]
    ):
        raise TemporalError("temporal_environment_mismatch")
    series = service.series(spec.series_id)
    refinement, original, baseline, candidate = service.lineage(
        spec.source_refinement_run_id, series
    )
    if (
        canonical_hash(refinement) != spec.provenance["refinement_report_hash"]
        or canonical_hash(series) != spec.provenance["series_hash"]
    ):
        raise TemporalError("temporal_lineage_changed")
    # In spark_test mode the accepted environment report is checked before any new execution.
    ExperimentService(service.session, service.state.query_executor, service.state.settings).gates(
        original
    )
    approvals = {}
    for item in spec.slice_artifacts:
        artifact = get_row(service.session, DevelopmentArtifactRow, item.artifact_id)
        evidence = baseline if item.role == "baseline" else candidate
        time_slice = next(s for s in series.slices if s.time_slice_id == item.time_slice_id)
        if (
            artifact.metric_ir_hash != evidence.metric_ir_hash
            or artifact.artifact_hash != item.content_hash
            or datetime.fromisoformat(artifact.snapshot["execution_context"]["anchor_time"])
            != time_slice.observation_time
        ):
            raise TemporalError("temporal_artifact_changed")
        approval = service.session.scalar(
            select(ApprovalRecordRow).where(
                ApprovalRecordRow.artifact_id == item.artifact_id,
                ApprovalRecordRow.decision == "approved",
            )
        )
        if approval is None:
            raise TemporalError("temporal_sql_not_approved")
        ApprovalService(service.session).require_approved(approval.approval_id, item.artifact_id)
        approvals[item.artifact_id] = approval.approval_id
    changed = service.session.execute(
        update(TemporalRunRow)
        .where(
            TemporalRunRow.temporal_validation_run_id == run_id,
            TemporalRunRow.status == "pending_sql_review",
        )
        .values(status="running", started_at=utcnow())
    )
    if changed.rowcount != 1:
        service.session.rollback()
        raise TemporalError("temporal_already_run")
    service.session.commit()
    report = report.model_copy(update={"status": "running"})
    service.save(report)
    matrix, references = [], {}
    try:
        for time_slice in sorted(series.slices, key=lambda s: s.observation_time):
            item = {
                "time_slice_id": time_slice.time_slice_id,
                "name": time_slice.name,
                "role": time_slice.role,
                "dataset_snapshot_id": time_slice.dataset_snapshot_id,
            }
            evidence_pair = []
            environment_keys = []
            for role in ("baseline", "candidate"):
                artifact = next(
                    a
                    for a in spec.slice_artifacts
                    if a.time_slice_id == time_slice.time_slice_id and a.role == role
                )
                approved = approvals[artifact.artifact_id]
                tested = TestingWorkflow(
                    service.session,
                    service.state.query_executor,
                    service.state.skills,
                ).run(
                    ExecutionRequest(
                        artifact_id=artifact.artifact_id, approval_id=approved, max_rows=1000
                    ),
                    request_id,
                )
                if tested.report.status not in ("passed", "passed_with_warnings"):
                    raise TemporalError("temporal_metric_test_failed")
                data = original.model_dump(mode="json")
                data.pop("experiment_spec_id")
                data.update(
                    experiment_name=f"temporal_{role}",
                    metric={"artifact_id": artifact.artifact_id, "artifact_version": "1.0.0"},
                    approval_id=approved,
                    test_run_id=tested.report.test_run_id,
                    dataset_snapshot_id=time_slice.dataset_snapshot_id,
                    anchor_time=time_slice.observation_time.isoformat(),
                    observation_time=time_slice.observation_time.isoformat(),
                    label_window=time_slice.label_window.model_dump(mode="json"),
                )
                aggregate = {}

                def observe(samples, evaluation):
                    values = [value for value, _ in samples]
                    if time_slice.time_slice_id == spec.reference_slice_id:
                        references[role] = (
                            values,
                            fixed_boundaries(values, spec.evaluation_policy.bins),
                            [
                                t
                                for t in evaluation.threshold_candidates
                                if set(t.sources) & {"p90", "p95", "ks_best_split"}
                            ],
                        )
                    values_ref, bins, thresholds = references[role]
                    aggregate.update(
                        psi=psi(
                            values_ref, values, bins, spec.stability_policy.psi_epsilon
                        ).model_dump(mode="json"),
                        frozen_thresholds=[
                            frozen_threshold(samples, t).model_dump(mode="json") for t in thresholds
                        ],
                    )

                experiments = ExperimentService(
                    service.session,
                    service.state.query_executor,
                    service.state.settings,
                    sample_observer=observe,
                )
                experiment_spec = experiments.create(decode(ExperimentSpec, data))
                result = experiments.run(experiment_spec.experiment_spec_id, request_id)
                if result.run.status != "completed":
                    raise TemporalError("temporal_experiment_failed")
                evidence = EvidenceExtractor(service.session, service.state.skills).extract(
                    result.run.experiment_run_id
                )
                evidence_pair.append(evidence)
                environment_keys.append(
                    {
                        key: result.reproducibility.get(key)
                        for key in ("environment_validation_run_id", "environment_report_hash")
                    }
                    | {
                        "source_mapping": result.reproducibility.get("execution_metadata", {}).get(
                            "source_mapping"
                        )
                    }
                )
                value = result.evaluation
                item[role] = {
                    "artifact_id": artifact.artifact_id,
                    "artifact_hash": artifact.content_hash,
                    "experiment_run_id": result.run.experiment_run_id,
                    "experiment_report_hash": canonical_hash(result),
                    "test_run_id": tested.report.test_run_id,
                    "approval_id": approved,
                    "environment": environment_keys[-1],
                    "coverage": value.coverage,
                    "ks": value.ks.value,
                    "iv": value.iv,
                    "direction": value.ks.direction,
                    "labeled_sample": value.labeled_sample,
                    "warnings": result.warnings,
                    **aggregate,
                }
            if environment_keys[0] != environment_keys[1] or (
                matrix and environment_keys[0] != matrix[0]["baseline"]["environment"]
            ):
                raise TemporalError("temporal_environment_mismatch")
            item["comparison"] = (
                BaselineCandidateComparison()
                .compare(*evidence_pair, refinement.plan.comparison_policy)
                .model_dump(mode="json")
            )
            matrix.append(item)
            logging.getLogger("airi.temporal").info(
                "temporal_slice_completed",
                extra={
                    "request_id": request_id,
                    "temporal_validation_run_id": run_id,
                    "series_id": series.series_id,
                    "time_slice_id": time_slice.time_slice_id,
                    "baseline_experiment_run_id": item["baseline"]["experiment_run_id"],
                    "candidate_experiment_run_id": item["candidate"]["experiment_run_id"],
                },
            )
        stability, findings, eligibility, status = TemporalDiagnostics().assess(
            matrix,
            spec.reference_slice_id,
            spec.oot_slice_id,
            spec.stability_policy,
            spec.promotion_policy,
            spec.evaluation_policy,
        )
        warnings = sorted(
            {
                w
                for item in matrix
                for role in ("baseline", "candidate")
                for w in item[role]["warnings"]
            }
        )
        missing = ["no production traffic", "no authenticated reviewer"]
        if report.synthetic_data:
            warnings.append(
                "synthetic evidence only; research demonstration, not real metric promotion"
            )
            missing.extend(["real Spark not verified", "real financial labels not verified"])
        if len(series.slices) < 12:
            missing.append("no seasonality coverage")
        if warnings and status == "passed":
            status = "passed_with_warnings"
        report = report.model_copy(
            update={
                "status": status,
                "historical": [
                    decode(TemporalSliceResult, m) for m in matrix if m["role"] == "historical"
                ],
                "oot": decode(OOTValidationResult, next(m for m in matrix if m["role"] == "oot")),
                "stability": stability,
                "diagnostics": findings,
                "promotion_eligibility": eligibility,
                "warnings": warnings,
                "missing_evidence": missing,
                "improvement_pattern": dict(Counter(m["comparison"]["outcome"] for m in matrix)),
                "threshold_transferability": {
                    "reference_slice_id": spec.reference_slice_id,
                    "method": "fixed numeric threshold and operator; NULL never hits",
                },
                "finished_at": datetime.now(UTC),
            }
        )
    except Exception as exc:
        report = report.model_copy(
            update={
                "status": "failed",
                "historical": [decode(TemporalSliceResult, m) for m in matrix],
                "failure_category": exc.code
                if isinstance(exc, TemporalError)
                else "temporal_validation_failed",
                "promotion_eligibility": "need_more_evidence",
                "finished_at": datetime.now(UTC),
            }
        )
    finally:
        references.clear()
    # Slice payloads are validated above; re-validate the whole wire schema as defense in depth.
    report = decode(type(report), report.model_dump(mode="json"))
    service.save(report)
    return report

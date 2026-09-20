import json

from pydantic import ValidationError

from airi.approvals.artifacts import artifact_content_hash, canonical_hash
from airi.approvals.persistence import DevelopmentArtifactRow
from airi.core.exceptions import AIRIError
from airi.experiments.models import (
    DatasetSnapshot,
    ExperimentReport,
    ExperimentSpec,
    LabelDefinition,
)
from airi.experiments.persistence import (
    DatasetSnapshotRow,
    ExperimentEvaluationRow,
    ExperimentRunRow,
    ExperimentSpecRow,
    LabelDefinitionRow,
)
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR
from airi.reflection.models import ComparisonMetric, MetricComparison, ReflectionEvidence


class ReflectionError(AIRIError):
    code = "reflection_invalid"
    status_code = 409


class ReflectionNotFound(ReflectionError):
    code = "reflection_resource_not_found"
    status_code = 404


class NotComparable(ReflectionError):
    code = "experiments_not_comparable"


def get_row(session, model, key):
    row = session.get(model, key, populate_existing=True)
    if row is None:
        raise ReflectionNotFound("Required persisted reflection or experiment resource not found")
    return row


def decode(model, value):
    return model.model_validate_json(json.dumps(value))


class EvidenceExtractor:
    def __init__(self, session, skills):
        self.session, self.skills = session, skills

    def extract(self, run_id):
        try:
            return self._extract(run_id)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise ReflectionError("experiment_provenance_invalid") from exc

    def _extract(self, run_id):
        row = get_row(self.session, ExperimentRunRow, run_id)
        saved = get_row(self.session, ExperimentEvaluationRow, run_id)
        report = decode(ExperimentReport, saved.report_json)
        if (
            row.status != "completed"
            or report.run.status != "completed"
            or report.evaluation is None
        ):
            raise ReflectionError("experiment_not_completed")
        spec = decode(
            ExperimentSpec,
            get_row(self.session, ExperimentSpecRow, row.experiment_spec_id).spec_json,
        )
        artifact = get_row(self.session, DevelopmentArtifactRow, spec.metric.artifact_id)
        dataset = decode(
            DatasetSnapshot,
            get_row(self.session, DatasetSnapshotRow, spec.dataset_snapshot_id).snapshot_json,
        )
        label = decode(
            LabelDefinition,
            get_row(self.session, LabelDefinitionRow, spec.label_definition_id).definition_json,
        )
        repro, evaluation = report.reproducibility, report.evaluation
        ir = decode(
            DerivedMetricIR
            if artifact.snapshot["metric_ir"].get("metric_type") == "derived"
            else MetricIR,
            artifact.snapshot["metric_ir"],
        )
        # Check linked identities and archived hashes. SQL is never included in LLM context.
        from airi.approvals.artifacts import DevelopmentArtifact

        draft = decode(DevelopmentArtifact, artifact.snapshot["artifact"])
        if (
            report.run.experiment_run_id != run_id
            or report.run.experiment_spec_id != spec.experiment_spec_id
            or report.run.artifact_id != artifact.artifact_id
            or report.run.dataset_snapshot_id != dataset.dataset_snapshot_id
            or report.run.label_definition_id != label.label_definition_id
            or repro["artifact_id"] != artifact.artifact_id
            or repro["artifact_hash"] != artifact.artifact_hash
            or artifact_content_hash(draft) != artifact.artifact_hash
            or canonical_hash(ir) != artifact.metric_ir_hash
            or repro["metric_ir_hash"] != artifact.metric_ir_hash
            or repro["experiment_spec_hash"] != canonical_hash(spec)
            or repro["dataset_snapshot"] != dataset.model_dump(mode="json")
            or repro["label_definition"] != label.model_dump(mode="json")
            or repro["evaluation_policy"] != spec.evaluation.model_dump(mode="json")
            or repro["skill_plan"] != artifact.snapshot["skill_plan"]
            or repro["tool_plan"] != artifact.snapshot["tool_plan"]
            or repro["template"] != artifact.snapshot["artifact"]["template"]
            or evaluation.metric_name != artifact.metric_name
            or saved.metric_name != artifact.metric_name
            or (saved.coverage, saved.ks, saved.iv, saved.bad_rate)
            != (evaluation.coverage, evaluation.ks.value, evaluation.iv, evaluation.bad_rate)
        ):
            raise ReflectionError("experiment_provenance_invalid")
        scenario_ref = artifact.snapshot["skill_plan"]["scenario_skill"]
        scenario = self.skills.get(scenario_ref["name"], scenario_ref["version"])
        return ReflectionEvidence(
            experiment_run_id=run_id,
            experiment_report_hash=canonical_hash(report),
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.artifact_version,
            artifact_hash=artifact.artifact_hash,
            metric_ir_hash=artifact.metric_ir_hash,
            dataset_snapshot_id=dataset.dataset_snapshot_id,
            label_definition_id=label.label_definition_id,
            observation_time=spec.observation_time,
            label_window=spec.label_window,
            evaluation_policy=spec.evaluation,
            evaluation=evaluation,
            join_summary=report.join_summary,
            warnings=list(dict.fromkeys(report.warnings + evaluation.warnings)),
            synthetic_data=(
                report.execution_mode == "mock"
                or bool(repro.get("execution_metadata", {}).get("source_mapping"))
            ),
            metric_ir=ir,
            scenario_skill=scenario,
        )


class ComparisonCompatibilityGuard:
    def validate(self, evidence):
        def key(item):
            # Version equality alone is insufficient when policy parameters differ.
            return (
                item.dataset_snapshot_id,
                item.label_definition_id,
                item.observation_time,
                item.label_window,
                item.evaluation_policy,
                item.synthetic_data,
            )

        if any(key(item) != key(evidence[0]) for item in evidence[1:]):
            raise NotComparable(
                "Experiments must share dataset, label, observation, label window "
                "and full evaluation policy, including execution provenance"
            )
        return MetricComparison(
            experiment_runs=[e.experiment_run_id for e in evidence],
            metrics=[
                ComparisonMetric(
                    experiment_run_id=e.experiment_run_id,
                    metric_name=e.evaluation.metric_name,
                    coverage=e.evaluation.coverage,
                    ks=e.evaluation.ks.value,
                    iv=e.evaluation.iv,
                )
                for e in evidence
            ],
        )

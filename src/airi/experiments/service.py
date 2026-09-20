import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from airi.approvals.persistence import DevelopmentArtifactRow
from airi.approvals.service import ApprovalService, utcnow
from airi.core.exceptions import AIRIError, DuplicateRegistrationError
from airi.environments.models import EnvironmentValidationReport
from airi.environments.persistence import EnvironmentValidationRow
from airi.execution.persistence import ExecutionRunRow
from airi.experiments.models import (
    DatasetSnapshot,
    ExperimentReport,
    LabelDefinition,
)
from airi.experiments.persistence import (
    DatasetSnapshotRow,
    ExperimentEvaluationRow,
    ExperimentSpecRow,
    LabelDefinitionRow,
)
from airi.experiments.validation import ExperimentError, LeakageValidator
from airi.testing.models import MetricTestReport
from airi.testing.persistence import MetricTestRunRow


class ExperimentNotFound(AIRIError):
    status_code = 404
    code = "experiment_resource_not_found"


class ExperimentService:
    def __init__(self, session, executor, settings, *, sample_observer=None):
        self.session, self.executor, self.settings = session, executor, settings
        # Internal synchronous aggregation hook; rows never become an API field.
        self.sample_observer = sample_observer

    def get_row(self, model, key):
        row = self.session.get(model, key, populate_existing=True)
        if row is None:
            raise ExperimentNotFound("dataset_not_found or experiment resource not found")
        return row

    def commit_new(self, row):
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise DuplicateRegistrationError(
                "Resource ID is already registered or invalid"
            ) from None

    def register_dataset(self, snapshot):
        self.commit_new(
            DatasetSnapshotRow(
                dataset_snapshot_id=snapshot.dataset_snapshot_id,
                snapshot_json=snapshot.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return snapshot

    def register_label(self, label):
        self.commit_new(
            LabelDefinitionRow(
                label_definition_id=label.label_definition_id,
                definition_json=label.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return label

    def latest_dataset(self, name: str | None = None) -> DatasetSnapshot | None:
        """Read-only lookup used by the Web demo to reuse a seeded snapshot.

        `name` pins the seeded demo snapshot (e.g. invoice_sample_20260909);
        later temporal-slice snapshots must not shadow it.
        """
        rows = (
            self.session.query(DatasetSnapshotRow)
            .order_by(DatasetSnapshotRow.created_at.desc())
            .all()
        )
        for row in rows:
            snapshot = DatasetSnapshot.model_validate_json(json.dumps(row.snapshot_json))
            if name is None or snapshot.name == name:
                return snapshot
        return None

    def latest_label(self, name: str | None = None) -> LabelDefinition | None:
        """Read-only lookup for the Web demo; `name` pins the seeded label so a
        second scenario's label cannot shadow the one the demo asked for."""
        rows = (
            self.session.query(LabelDefinitionRow)
            .order_by(LabelDefinitionRow.created_at.desc())
            .all()
        )
        for row in rows:
            label = LabelDefinition.model_validate_json(json.dumps(row.definition_json))
            if name is None or label.name == name:
                return label
        return None

    def gates(self, spec):
        mode = self.settings.effective_execution_mode
        if mode not in ("mock", "spark_test") or self.settings.environment == "production":
            raise ExperimentError("experiment_execution_disabled")
        ApprovalService(self.session).require_approved(spec.approval_id, spec.metric.artifact_id)
        artifact = self.get_row(DevelopmentArtifactRow, spec.metric.artifact_id)
        if artifact.artifact_version != spec.metric.artifact_version:
            raise ExperimentError("artifact_version_mismatch")
        LeakageValidator().validate(spec)
        if (
            datetime.fromisoformat(artifact.snapshot["execution_context"]["anchor_time"])
            != spec.anchor_time
        ):
            raise ExperimentError("metric_anchor_mismatch")
        test = self.get_row(MetricTestRunRow, spec.test_run_id)
        report = MetricTestReport.model_validate_json(json.dumps(test.report_json))
        execution = self.get_row(ExecutionRunRow, test.execution_run_id)
        expected_hash = hashlib.sha256(
            artifact.snapshot["artifact"]["code"].encode("utf-8")
        ).hexdigest()
        if (
            test.artifact_id != artifact.artifact_id
            or report.test_run_id != test.test_run_id
            or report.execution_run_id != execution.execution_run_id
            or report.failed != 0
            or report.artifact_id != artifact.artifact_id
            or execution.artifact_id != artifact.artifact_id
            or execution.sql_hash != expected_hash
            or execution.status != "success"
            or execution.truncated
            or test.status not in ("passed", "passed_with_warnings")
            or report.status != test.status
        ):
            raise ExperimentError("metric_test_not_passed")
        environment = None
        if mode == "spark_test":
            if not spec.environment_validation_run_id:
                raise ExperimentError("environment_not_accepted")
            environment = self.get_row(EnvironmentValidationRow, spec.environment_validation_run_id)
            observed = EnvironmentValidationReport.model_validate_json(
                json.dumps(environment.report_json)
            )
            if (
                environment.status not in ("passed", "passed_with_warnings")
                or observed.overall != environment.status
                or observed.connectivity != "passed"
                or observed.session_timezone != "Asia/Shanghai"
            ):
                raise ExperimentError("environment_not_accepted")
        dataset = DatasetSnapshot.model_validate_json(
            json.dumps(self.get_row(DatasetSnapshotRow, spec.dataset_snapshot_id).snapshot_json)
        )
        label = LabelDefinition.model_validate_json(
            json.dumps(self.get_row(LabelDefinitionRow, spec.label_definition_id).definition_json)
        )
        if dataset.snapshot_time != spec.observation_time:
            raise ExperimentError("dataset_invalid: snapshot must match observation time")
        return artifact, dataset, label, environment, report

    def create(self, spec):
        self.gates(spec)
        self.commit_new(
            ExperimentSpecRow(
                experiment_spec_id=spec.experiment_spec_id,
                name=spec.experiment_name,
                artifact_id=spec.metric.artifact_id,
                dataset_snapshot_id=spec.dataset_snapshot_id,
                label_definition_id=spec.label_definition_id,
                anchor_time=spec.anchor_time.astimezone(UTC).replace(tzinfo=None),
                spec_json=spec.model_dump(mode="json"),
                created_at=utcnow(),
            )
        )
        return spec

    def run(self, spec_id, request_id):
        from airi.workflows.experiments.workflow import run_experiment

        return run_experiment(self, spec_id, request_id)

    def report(self, run_id):
        row = self.get_row(ExperimentEvaluationRow, run_id)
        return ExperimentReport.model_validate_json(json.dumps(row.report_json))

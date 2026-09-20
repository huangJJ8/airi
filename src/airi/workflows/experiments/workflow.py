import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from airi.approvals.artifacts import canonical_hash
from airi.approvals.service import utcnow
from airi.evaluation.calculator import calculate
from airi.execution.models import ExecutionRequest
from airi.execution.service import ExecutionService
from airi.experiments.models import (
    ExperimentExecutionPlan,
    ExperimentReport,
    ExperimentRun,
    ExperimentSpec,
)
from airi.experiments.persistence import (
    ExperimentEvaluationRow,
    ExperimentRunRow,
    ExperimentSpecRow,
)
from airi.experiments.validation import ExperimentError, evaluation_dataset


def run_experiment(service, spec_id, request_id):
    spec = ExperimentSpec.model_validate_json(
        json.dumps(service.get_row(ExperimentSpecRow, spec_id).spec_json)
    )
    artifact, snapshot, label, environment, tests = service.gates(spec)
    mode = service.settings.effective_execution_mode
    run = ExperimentRun(
        experiment_spec_id=spec_id,
        artifact_id=artifact.artifact_id,
        dataset_snapshot_id=snapshot.dataset_snapshot_id,
        label_definition_id=label.label_definition_id,
        status="running",
        started_at=datetime.now(UTC),
    )
    row = ExperimentRunRow(
        experiment_run_id=run.experiment_run_id,
        experiment_spec_id=spec_id,
        status="running",
        started_at=utcnow(),
        finished_at=None,
        failure_category=None,
        created_at=utcnow(),
        run_json=run.model_dump(mode="json"),
    )
    service.commit_new(row)
    evaluation, joins, warnings = None, {}, []
    if mode == "mock":
        warnings.append(
            "SYNTHETIC MOCK EXPERIMENT; not real Spark or business effectiveness evidence"
        )
    if tests.status == "passed_with_warnings":
        warnings.append("Prior metric test passed_with_warnings; review its evidence")
    repro = {
        "artifact_id": artifact.artifact_id,
        "artifact_hash": artifact.artifact_hash,
        "metric_ir_hash": artifact.metric_ir_hash,
        "skill_plan": artifact.snapshot["skill_plan"],
        "tool_plan": artifact.snapshot["tool_plan"],
        "template": artifact.snapshot["artifact"]["template"],
        "evaluation_dataset_template": {"name": "evaluation_dataset", "version": "1.0.0"},
        "dataset_snapshot": snapshot.model_dump(mode="json"),
        "label_definition": label.model_dump(mode="json"),
        "anchor_time": spec.anchor_time.isoformat(),
        "evaluation_policy": spec.evaluation.model_dump(),
        "environment_validation_run_id": spec.environment_validation_run_id,
        "environment_report_hash": canonical_hash(environment.report_json) if environment else None,
        "test_run_id": spec.test_run_id,
        "test_report_hash": canonical_hash(tests),
        "experiment_spec_hash": canonical_hash(spec),
    }
    try:
        executed, _, _ = ExecutionService(service.session, service.executor).run(
            ExecutionRequest(
                artifact_id=artifact.artifact_id, approval_id=spec.approval_id, max_rows=1000
            ),
            request_id,
        )
        if executed.status != "success" or executed.truncated:
            raise ExperimentError("evaluation_failed: execution failed or incomplete")
        plan = ExperimentExecutionPlan.model_validate(
            {
                **{
                    k: getattr(executed, k)
                    for k in ExperimentExecutionPlan.model_fields
                    if hasattr(executed, k)
                },
                "dataset_snapshot_id": snapshot.dataset_snapshot_id,
                "label_definition_id": label.label_definition_id,
                "execution_mode": mode,
            }
        )
        data = service.executor.load_dataset(snapshot, plan)
        if data.truncated:
            raise ExperimentError("dataset_invalid: truncated dataset")
        samples, joins = evaluation_dataset(
            executed.rows, data.rows, artifact.metric_name, snapshot, label
        )
        metric_checksum = canonical_hash(sorted(executed.rows, key=lambda r: str(r["entity_id"])))
        previous = service.session.scalars(
            select(ExperimentEvaluationRow)
            .join(ExperimentRunRow)
            .where(
                ExperimentRunRow.experiment_spec_id == spec_id,
                ExperimentRunRow.status == "completed",
            )
        ).first()
        if (
            previous
            and previous.report_json["reproducibility"]["metric_result_checksum"] != metric_checksum
        ):
            raise ExperimentError("dataset_invalid: metric source changed since first run")
        evaluation = calculate(artifact.metric_name, samples, len(data.rows), spec.evaluation)
        if service.sample_observer is not None:
            service.sample_observer(samples, evaluation)
        warnings.extend(evaluation.warnings)
        repro.update(
            {
                "execution_run_id": executed.execution_run_id,
                "execution_metadata": executed.model_dump(mode="json", exclude={"rows", "columns"}),
                "metric_result_checksum": canonical_hash(
                    sorted(executed.rows, key=lambda r: str(r["entity_id"]))
                ),
            }
        )
        run = run.model_copy(
            update={
                "status": "completed",
                "metric_row_count": executed.row_count,
                "labeled_row_count": len(samples),
                "finished_at": datetime.now(UTC),
            }
        )
    except Exception as exc:
        category = exc.category if isinstance(exc, ExperimentError) else "evaluation_failed"
        run = run.model_copy(
            update={
                "status": "failed",
                "failure_category": category,
                "finished_at": datetime.now(UTC),
            }
        )
        warnings.append(category)
    result = ExperimentReport(
        run=run,
        execution_mode=mode,
        evaluation=evaluation,
        join_summary=joins,
        reproducibility=repro,
        leakage_check={
            "status": "passed",
            "metric_anchor_time": spec.anchor_time.isoformat(),
            "label_window": spec.label_window.model_dump(mode="json"),
        },
        warnings=warnings,
    )
    row.status, row.failure_category = run.status, run.failure_category
    row.finished_at, row.run_json = utcnow(), run.model_dump(mode="json")
    service.session.add(
        ExperimentEvaluationRow(
            experiment_run_id=run.experiment_run_id,
            metric_name=artifact.metric_name,
            coverage=evaluation.coverage if evaluation else None,
            bad_rate=evaluation.bad_rate if evaluation else None,
            ks=evaluation.ks.value if evaluation else None,
            iv=evaluation.iv if evaluation else None,
            risk_direction=evaluation.ks.direction if evaluation else "undetermined",
            report_json=result.model_dump(mode="json"),
            created_at=utcnow(),
        )
    )
    service.session.commit()
    logging.getLogger("airi.experiment").info(
        "experiment_completed",
        extra={
            "request_id": request_id,
            "workflow_run_id": artifact.workflow_run_id,
            "artifact_id": artifact.artifact_id,
            "test_run_id": spec.test_run_id,
            "experiment_spec_id": spec_id,
            "experiment_run_id": run.experiment_run_id,
            "dataset_snapshot_id": snapshot.dataset_snapshot_id,
            "status": run.status,
        },
    )
    return result

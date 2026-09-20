import hashlib
import logging
from datetime import UTC, datetime
from time import perf_counter

from pydantic import TypeAdapter

from airi.approvals.persistence import DevelopmentArtifactRow
from airi.approvals.service import ApprovalService, utcnow
from airi.core.execution import ExecutionContext
from airi.execution.models import ExecutionPlan, ExecutionProfile, ExecutionResult, QueryOutput
from airi.execution.persistence import ExecutionRunRow
from airi.execution.sandbox import SandboxGuard
from airi.infrastructure.query_executor import QueryFailure, classify_provider_error
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR

logger = logging.getLogger("airi.execution")


class ExecutionService:
    def __init__(self, session, executor):
        self.session, self.executor = session, executor

    def run(self, request, request_id):
        approval = ApprovalService(self.session).require_approved(
            request.approval_id, request.artifact_id
        )
        artifact = self.session.get(DevelopmentArtifactRow, request.artifact_id)
        metric = TypeAdapter(MetricIR | DerivedMetricIR).validate_python(
            artifact.snapshot["metric_ir"]
        )
        context = ExecutionContext.model_validate(artifact.snapshot["execution_context"])
        code = artifact.snapshot["artifact"]["code"]
        profile = ExecutionProfile()
        SandboxGuard().validate(code, metric, context, profile)
        plan = ExecutionPlan(
            **request.model_dump(),
            workflow_run_id=approval.workflow_run_id,
            request_id=request_id,
            execution_context=context,
            sql_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        )
        start = datetime.now(UTC)
        row = ExecutionRunRow(
            execution_run_id=plan.execution_run_id,
            workflow_run_id=plan.workflow_run_id,
            artifact_id=plan.artifact_id,
            approval_id=plan.approval_id,
            execution_profile=plan.execution_profile,
            engine=plan.engine,
            sql_hash=plan.sql_hash,
            status="running",
            started_at=utcnow(),
            created_at=utcnow(),
            metadata_json=plan.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.commit()
        started = perf_counter()
        category, message, output = None, None, QueryOutput(columns=[], rows=[])
        try:
            output = QueryOutput.model_validate(self.executor.execute(code, plan).model_dump())
            if perf_counter() - started > plan.timeout_seconds:
                raise QueryFailure("timeout")
            status = "success"
        except Exception as exc:
            category = classify_provider_error(exc)
            status = "timeout" if category == "timeout" else "failed"
            message = f"Test execution failed ({category}); contact the test environment operator"
            output = QueryOutput(
                columns=[], rows=[], cancel_requested=getattr(exc, "cancel_requested", None)
            )
        truncated = output.truncated or len(output.rows) > plan.max_rows
        rows = output.rows[: plan.max_rows]
        result = ExecutionResult(
            **plan.model_dump(),
            status=status,
            started_at=start,
            finished_at=datetime.now(UTC),
            duration_ms=(perf_counter() - started) * 1000,
            failure_category=category,
            error_message=message,
            columns=output.columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            **{
                key: getattr(output, key)
                for key in (
                    "provider_query_id",
                    "cancel_requested",
                    "remote_cancel_status",
                    "executed_sql_hash",
                    "source_mapping",
                )
            },
            warnings=["Result truncated; global data quality is not established"]
            if truncated
            else [],
        )
        for key in ("status", "row_count", "truncated", "failure_category", "error_message"):
            setattr(row, key, getattr(result, key))
        row.finished_at = utcnow()
        row.duration_ms = round(result.duration_ms)
        row.metadata_json = result.model_dump(mode="json", exclude={"rows", "columns"})
        self.session.commit()
        logger.info("execution_completed", extra=result.model_dump(exclude={"rows", "columns"}))
        return result, metric, code

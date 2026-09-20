import logging

from airi.core.schemas import StrictSchema
from airi.execution.models import ExecutionResult
from airi.execution.service import ExecutionService
from airi.metric_ir.semantics import scenario_for_metric
from airi.refinement.transform import scenario_for_candidate
from airi.testing.models import MetricTestReport
from airi.testing.persistence import MetricTestResultRow, MetricTestRunRow
from airi.testing.planning import MetricTestPlanner
from airi.testing.runner import MetricTestRunner


class TestingResponse(StrictSchema):
    execution: ExecutionResult
    report: MetricTestReport


class TestingWorkflow:
    """Test rules come from the artifact's own declaration, never from a default.

    The stored Metric IR decides which scenario declared it, so a relationship
    artifact is tested with relationship checks and an invoice artifact with
    invoice checks, with no scenario field on the request. A window-review
    candidate belongs to its baseline's scenario family.
    """

    def __init__(self, session, executor, skills):
        self.session, self.executor, self.skills = session, executor, skills

    def rules_for(self, metric):
        scenario = scenario_for_candidate(metric) or scenario_for_metric(metric)
        return self.skills.get(scenario, "1.0.0").test_rules

    def run(self, request, request_id):
        execution, metric, code = ExecutionService(self.session, self.executor).run(
            request, request_id
        )
        rules = self.rules_for(metric)
        plan = MetricTestPlanner().plan(request.artifact_id, execution.execution_run_id, rules)
        report = MetricTestRunner().run(
            plan,
            execution,
            metric,
            code,
            rules,
            self.executor.fixture_rows,
            getattr(self.executor, "relation_tables", None),
        )
        metadata = report.model_dump(
            exclude={"plan", "results", "classifications", "requires_human_review"}
        )
        for key in ("created_at", "finished_at"):
            metadata[key] = metadata[key].replace(tzinfo=None)
        self.session.add(MetricTestRunRow(**metadata, report_json=report.model_dump(mode="json")))
        self.session.flush()
        for result in report.results:
            self.session.add(
                MetricTestResultRow(
                    test_case_id=result.test_case_id,
                    test_run_id=report.test_run_id,
                    test_type=result.type,
                    status=result.status,
                    severity=result.severity,
                    failure_category=result.failure_category,
                    expected_summary=result.expected_summary,
                    actual_summary=result.actual_summary,
                    details_json=result.details,
                    created_at=metadata["created_at"],
                )
            )
        self.session.commit()
        logging.getLogger("airi.testing").info(
            "metric_testing_completed",
            extra={
                **execution.model_dump(exclude={"rows", "columns"}),
                "test_run_id": report.test_run_id,
                "status": report.status,
                "passed": report.passed,
                "failed": report.failed,
                "warnings": report.warnings,
                "skipped": report.skipped,
            },
        )
        return TestingResponse(execution=execution, report=report)

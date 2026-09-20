import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.core.exceptions import AIRIError
from airi.execution.models import ExecutionRequest
from airi.infrastructure.database import get_session
from airi.testing.models import MetricTestReport
from airi.testing.persistence import MetricTestRunRow
from airi.workflows.testing.workflow import TestingResponse, TestingWorkflow

router = APIRouter(prefix="/api/v1/tests", tags=["metric-testing"])


class TestReportNotFound(AIRIError):
    code = "test_report_not_found"
    status_code = 404


@router.post("/run", response_model=TestingResponse)
def run_tests(payload: ExecutionRequest, request: Request, session: Session = Depends(get_session)):
    """Resolve the scenario from the stored artifact; no caller-supplied scenario."""
    return TestingWorkflow(session, request.app.state.query_executor, request.app.state.skills).run(
        payload, request.state.request_id
    )


@router.get("/{test_run_id}", response_model=MetricTestReport)
def get_report(test_run_id: str, session: Session = Depends(get_session)):
    row = session.get(MetricTestRunRow, test_run_id)
    if row is None:
        raise TestReportNotFound("Test report not found")
    return MetricTestReport.model_validate_json(json.dumps(row.report_json))

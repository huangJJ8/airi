from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.core.schemas import StrictSchema
from airi.environments.models import EnvironmentStatus, EnvironmentValidationReport
from airi.environments.service import SparkEnvironmentProbe, save_report
from airi.execution.sandbox import SandboxViolation
from airi.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1/environments/spark-test", tags=["environment-acceptance"])


class ValidationRequest(StrictSchema):
    """No caller-controlled connection, SQL, source, or credentials."""


def probe_for(request):
    settings = request.app.state.settings
    if settings.effective_execution_mode != "spark_test" or settings.environment == "production":
        raise SandboxViolation("Environment API requires an internal spark_test deployment")
    return SparkEnvironmentProbe(request.app.state.query_executor, settings)


@router.get("/status", response_model=EnvironmentStatus)
def status(request: Request):
    return probe_for(request).status()


@router.post("/validate", response_model=EnvironmentValidationReport)
def validate(
    request: Request,
    payload: ValidationRequest | None = None,
    session: Session = Depends(get_session),
):
    report = probe_for(request).validate()
    save_report(session, report, request.state.request_id)
    return report

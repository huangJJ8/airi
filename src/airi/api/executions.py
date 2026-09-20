from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.execution.models import ExecutionRequest, ExecutionResult
from airi.execution.service import ExecutionService
from airi.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1/executions", tags=["test-execution"])


@router.post("", response_model=ExecutionResult)
def execute(payload: ExecutionRequest, request: Request, session: Session = Depends(get_session)):
    result, _, _ = ExecutionService(session, request.app.state.query_executor).run(
        payload, request.state.request_id
    )
    return result

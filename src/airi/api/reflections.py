from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.infrastructure.database import get_session
from airi.reflection.models import ReflectionReport, ReflectionSpec
from airi.workflows.reflection.workflow import ReflectionWorkflow

router = APIRouter(prefix="/api/v1/reflections", tags=["reflections"])


def workflow(request: Request, session: Session = Depends(get_session)):
    state = request.app.state
    return ReflectionWorkflow(
        session,
        state.skills,
        state.reflection_llm,
        state.reflection_policy,
        state.reflection_model,
        state.reflection_output_kind,
    )


@router.post("", response_model=ReflectionReport, status_code=201)
def create(payload: ReflectionSpec, request: Request, service=Depends(workflow)):
    return service.run(payload, request.state.request_id)


@router.get("/{reflection_run_id}", response_model=ReflectionReport)
def get(reflection_run_id: str, service=Depends(workflow)):
    return service.get(reflection_run_id)

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.approvals.artifacts import ReviewedArtifact
from airi.approvals.service import ApprovalService
from airi.infrastructure.database import get_session
from airi.workflows.development.schemas import DevelopmentRequest, DevelopmentResult
from airi.workflows.development.workflow import DevelopmentWorkflow

router = APIRouter(prefix="/api/v1/development", tags=["development"])


def get_development_workflow(request: Request) -> DevelopmentWorkflow:
    return request.app.state.development_workflow


@router.post("/generate", response_model=DevelopmentResult)
def generate_draft(
    payload: DevelopmentRequest,
    request: Request,
    workflow: DevelopmentWorkflow = Depends(get_development_workflow),
    session: Session = Depends(get_session),
) -> DevelopmentResult:
    """Generate a review-only Spark SQL draft. Never execute SQL."""
    result = workflow.run(payload)
    ApprovalService(session).save_draft(result)
    logging.getLogger("airi.development").info(
        "draft_saved",
        extra={
            "request_id": request.state.request_id,
            "workflow_run_id": result.workflow_run_id,
            "artifact_id": result.artifact.artifact_id,
            "status": "draft",
        },
    )
    return result


@router.get("/artifacts/{artifact_id}", response_model=ReviewedArtifact)
def get_artifact(artifact_id: str, session: Session = Depends(get_session)) -> ReviewedArtifact:
    return ApprovalService(session).get_artifact(artifact_id)

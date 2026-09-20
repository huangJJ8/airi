import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.approvals.schemas import ApprovalRecord, ReviewDecision, SubmitReview
from airi.approvals.service import ApprovalService
from airi.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.post("", response_model=ApprovalRecord, status_code=201)
def submit_review(payload: SubmitReview, session: Session = Depends(get_session)):
    return ApprovalService(session).submit(payload)


@router.get("/{approval_id}", response_model=ApprovalRecord)
def get_review(approval_id: str, session: Session = Depends(get_session)):
    return ApprovalService(session).get(approval_id)


@router.post("/{approval_id}/approve", response_model=ApprovalRecord)
def approve(
    approval_id: str,
    payload: ReviewDecision,
    request: Request,
    session: Session = Depends(get_session),
):
    record = ApprovalService(session).decide(approval_id, "approved", payload)
    logging.getLogger("airi.approval").info(
        "artifact_approved",
        extra={
            "request_id": request.state.request_id,
            "workflow_run_id": record.workflow_run_id,
            "artifact_id": record.artifact_id,
            "approval_id": record.approval_id,
            "status": "approved",
        },
    )
    return record


@router.post("/{approval_id}/reject", response_model=ApprovalRecord)
def reject(approval_id: str, payload: ReviewDecision, session: Session = Depends(get_session)):
    return ApprovalService(session).decide(approval_id, "rejected", payload)

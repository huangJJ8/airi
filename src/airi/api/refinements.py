from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.core.schemas import StrictSchema
from airi.infrastructure.database import get_session
from airi.refinement.models import (
    ProposalDecision,
    ProposalDecisionInput,
    ProposalView,
    RefinementDecision,
    RefinementReport,
    RefinementRequest,
)
from airi.refinement.service import RefinementService

router = APIRouter(prefix="/api/v1", tags=["refinements"])


def service(request: Request, session: Session = Depends(get_session)):
    return RefinementService(session, request.app.state)


@router.get("/reflections/{reflection_id}/proposals", response_model=list[ProposalView])
def proposals(reflection_id: str, app=Depends(service)):
    return app.proposals(reflection_id)


@router.post(
    "/reflections/{reflection_id}/proposals/{proposal_id}/decision", response_model=ProposalDecision
)
def decide(
    reflection_id: str, proposal_id: str, payload: ProposalDecisionInput, app=Depends(service)
):
    return app.decide(reflection_id, proposal_id, payload)


@router.post("/refinements", response_model=RefinementReport, status_code=201)
def create(payload: RefinementRequest, app=Depends(service)):
    return app.create(payload)


class EvaluateRequest(StrictSchema):
    pass


@router.post("/refinements/{run_id}/evaluate", response_model=RefinementReport)
def evaluate(
    run_id: str, request: Request, payload: EvaluateRequest | None = None, app=Depends(service)
):
    return app.evaluate(run_id, request.state.request_id)


@router.get("/refinements/{run_id}", response_model=RefinementReport)
def get(run_id: str, app=Depends(service)):
    return app.get(run_id)


@router.post("/refinements/{run_id}/decision", response_model=RefinementReport)
def final_decision(run_id: str, payload: RefinementDecision, app=Depends(service)):
    return app.final_decision(run_id, payload)

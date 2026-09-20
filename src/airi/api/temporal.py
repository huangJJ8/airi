from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.api.refinements import EvaluateRequest
from airi.infrastructure.database import get_session
from airi.temporal.models import (
    PromotionDecision,
    PromotionReview,
    PromotionReviewRequest,
    TemporalDatasetSeries,
    TemporalSeriesInput,
    TemporalValidationReport,
    TemporalValidationRequest,
)
from airi.temporal.service import TemporalService

router = APIRouter(prefix="/api/v1", tags=["temporal validation"])


def service(request: Request, session: Session = Depends(get_session)):
    return TemporalService(session, request.app.state)


@router.post("/temporal-series", response_model=TemporalDatasetSeries, status_code=201)
def series(payload: TemporalSeriesInput, app=Depends(service)):
    return app.register_series(payload)


@router.post("/temporal-validations", response_model=TemporalValidationReport, status_code=201)
def create(payload: TemporalValidationRequest, app=Depends(service)):
    return app.create(payload)


@router.post("/temporal-validations/{run_id}/run", response_model=TemporalValidationReport)
def run(
    run_id: str, request: Request, payload: EvaluateRequest | None = None, app=Depends(service)
):
    return app.run(run_id, request.state.request_id)


@router.get("/temporal-validations/{run_id}", response_model=TemporalValidationReport)
def get(run_id: str, app=Depends(service)):
    return app.get(run_id)


@router.post("/promotion-reviews", response_model=PromotionReview, status_code=201)
def review(payload: PromotionReviewRequest, app=Depends(service)):
    return app.create_review(payload)


@router.get("/promotion-reviews/{review_id}", response_model=PromotionReview)
def get_review(review_id: str, app=Depends(service)):
    return app.review(review_id)


@router.post("/promotion-reviews/{review_id}/decision", response_model=PromotionReview)
def decide(review_id: str, payload: PromotionDecision, request: Request, app=Depends(service)):
    return app.decide(review_id, payload, request.state.request_id)

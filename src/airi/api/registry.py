from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.api.refinements import EvaluateRequest
from airi.infrastructure.database import get_session
from airi.registry.models import (
    ActivationRequest,
    MetricDefinition,
    MetricRelease,
    MetricReleaseEvent,
    MetricVersion,
    MetricVersionDiff,
    MetricVersionRequest,
    ReleaseDecision,
    ReleaseRequest,
    ReleaseReview,
    ReleaseValidationReport,
    RollbackDecision,
    RollbackRequest,
    RollbackReview,
)
from airi.registry.service import RegistryService

router = APIRouter(prefix="/api/v1", tags=["metric registry"])


def service(request: Request, session: Session = Depends(get_session)):
    return RegistryService(session, request.app.state)


@router.get("/metrics", response_model=list[MetricDefinition])
def definitions(app=Depends(service)):
    return app.definitions()


@router.get("/metrics/{metric_key}", response_model=MetricDefinition)
def definition(metric_key: str, app=Depends(service)):
    return app.definition(metric_key)


@router.get("/metrics/{metric_key}/versions", response_model=list[MetricVersion])
def versions(metric_key: str, app=Depends(service)):
    return app.versions(metric_key)


@router.get("/metrics/{metric_key}/active", response_model=MetricVersion | None)
def active(metric_key: str, app=Depends(service)):
    """Exact-version registry reads only; this is the sole active-pointer lookup."""
    return app.active_version(metric_key)


@router.get("/metrics/{metric_key}/events", response_model=list[MetricReleaseEvent])
def events(metric_key: str, app=Depends(service)):
    return app.events(metric_key)


@router.get("/metrics/{metric_key}/versions/{version}", response_model=MetricVersion)
def version(metric_key: str, version: str, app=Depends(service)):
    return app.version(metric_key, version)


@router.get(
    "/metrics/{metric_key}/versions/{from_version}/compare/{to_version}",
    response_model=MetricVersionDiff,
)
def compare(metric_key: str, from_version: str, to_version: str, app=Depends(service)):
    """Deterministic semantic diff. No LLM summarisation."""
    return app.compare(metric_key, from_version, to_version)


@router.post("/metric-versions", response_model=MetricVersion, status_code=201)
def create_version(payload: MetricVersionRequest, app=Depends(service)):
    return app.create_version(payload)


@router.post("/releases", response_model=MetricRelease, status_code=201)
def create_release(payload: ReleaseRequest, app=Depends(service)):
    return app.create_release(payload)


@router.get("/releases/{release_id}", response_model=MetricRelease)
def get_release(release_id: str, app=Depends(service)):
    return app.release(release_id)


@router.post("/releases/{release_id}/validate", response_model=ReleaseValidationReport)
def validate(
    release_id: str,
    request: Request,
    payload: EvaluateRequest | None = None,
    app=Depends(service),
):
    return app.validate(release_id, request.state.request_id)


@router.get("/releases/{release_id}/validation", response_model=ReleaseValidationReport)
def validation(release_id: str, app=Depends(service)):
    return app.validation_report(release_id)


@router.post("/releases/{release_id}/review", response_model=ReleaseReview, status_code=201)
def create_review(release_id: str, app=Depends(service)):
    return app.create_review(release_id)


@router.get("/release-reviews/{release_review_id}", response_model=ReleaseReview)
def get_review(release_review_id: str, app=Depends(service)):
    return app.release_review(release_review_id)


@router.post("/release-reviews/{release_review_id}/decision", response_model=ReleaseReview)
def decide_review(
    release_review_id: str,
    payload: ReleaseDecision,
    request: Request,
    app=Depends(service),
):
    return app.decide_review(release_review_id, payload, request.state.request_id)


@router.post("/releases/{release_id}/activate", response_model=MetricRelease)
def activate(
    release_id: str,
    payload: ActivationRequest,
    request: Request,
    app=Depends(service),
):
    """Logical registry pointer switch. Never a Spark production deployment."""
    return app.activate(release_id, payload, request.state.request_id)


@router.post("/metrics/{metric_key}/rollback", response_model=RollbackReview, status_code=201)
def request_rollback(metric_key: str, payload: RollbackRequest, app=Depends(service)):
    return app.request_rollback(metric_key, payload)


@router.get("/rollback-reviews/{rollback_review_id}", response_model=RollbackReview)
def get_rollback(rollback_review_id: str, app=Depends(service)):
    return app.rollback_review(rollback_review_id)


@router.post("/rollback-reviews/{rollback_review_id}/decision", response_model=RollbackReview)
def decide_rollback(
    rollback_review_id: str,
    payload: RollbackDecision,
    request: Request,
    app=Depends(service),
):
    return app.decide_rollback(rollback_review_id, payload, request.state.request_id)

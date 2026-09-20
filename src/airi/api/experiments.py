from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from airi.core.schemas import StrictSchema
from airi.experiments.models import (
    DatasetSnapshot,
    ExperimentReport,
    ExperimentSpec,
    LabelDefinition,
)
from airi.experiments.service import ExperimentService
from airi.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1", tags=["experiments"])


class RunRequest(StrictSchema):
    """Execution accepts no SQL or other caller-controlled fields."""


def service(request: Request, session: Session = Depends(get_session)):
    return ExperimentService(session, request.app.state.query_executor, request.app.state.settings)


@router.post("/datasets", response_model=DatasetSnapshot, status_code=201)
def dataset(payload: DatasetSnapshot, app: ExperimentService = Depends(service)):
    return app.register_dataset(payload)


@router.post("/labels", response_model=LabelDefinition, status_code=201)
def label(payload: LabelDefinition, app: ExperimentService = Depends(service)):
    return app.register_label(payload)


@router.get("/datasets/latest", response_model=DatasetSnapshot | None)
def latest_dataset(name: str | None = None, app: ExperimentService = Depends(service)):
    """Read-only: lets the Web demo reuse the seeded synthetic snapshot.

    `name` pins the seeded demo snapshot so temporal-slice datasets do not
    shadow it.
    """
    return app.latest_dataset(name)


@router.get("/labels/latest", response_model=LabelDefinition | None)
def latest_label(name: str | None = None, app: ExperimentService = Depends(service)):
    return app.latest_label(name)


@router.post("/experiments", response_model=ExperimentSpec, status_code=201)
def create(payload: ExperimentSpec, app: ExperimentService = Depends(service)):
    return app.create(payload)


@router.post("/experiments/{spec_id}/run", response_model=ExperimentReport)
def run(
    spec_id: str,
    request: Request,
    payload: RunRequest | None = None,
    app: ExperimentService = Depends(service),
):
    return app.run(spec_id, request.state.request_id)


@router.get("/experiments/runs/{run_id}", response_model=ExperimentReport)
def report(run_id: str, app: ExperimentService = Depends(service)):
    return app.report(run_id)

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from airi import __version__
from airi.core.schemas import StrictSchema
from airi.metric_ir.models import MetricIR

router = APIRouter()


class HealthResponse(StrictSchema):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Process liveness only; does not assert database or LLM availability."""
    return HealthResponse()


class MetaResponse(StrictSchema):
    """Read-only runtime facts for the Web UI's honest-mode banner.

    Deliberately exposes no domain state: only what the demo needs to avoid
    pretending to be a production system. ``scenarios`` lists the registered
    scenario skills so the UI never hardcodes the demo families.
    """

    app_name: str
    version: str
    environment: Literal["local", "test", "production"]
    execution_mode: Literal["disabled", "mock", "spark_test"]
    llm_mode: Literal["openai_compatible", "demo_mock"]
    production_deployed: Literal[False] = False
    scenarios: list[str] = Field(default_factory=list)


@router.get("/api/v1/meta", response_model=MetaResponse, tags=["health"])
def meta(request: Request) -> MetaResponse:
    config = request.app.state.settings
    skills = request.app.state.skills
    return MetaResponse(
        app_name=config.app_name,
        version=__version__,
        environment=config.environment,
        execution_mode=config.effective_execution_mode,
        llm_mode=config.llm_mode,
        scenarios=sorted(
            {
                skill.name
                for skill in skills.list_skills()
                # Candidate scenario versions (e.g. invoice_risk@1.1.0) are the
                # same demo family; the UI lists distinct families.
                if skill.type == "scenario"
            }
        ),
    )


@router.post("/api/v1/metric-ir/validate", response_model=MetricIR, tags=["metric-ir"])
def validate_metric_ir(metric: MetricIR) -> MetricIR:
    """Validate structured semantics only. Does not generate, execute or approve SQL."""
    return metric

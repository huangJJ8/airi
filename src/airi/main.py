import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from airi import __version__
from airi.agents.requirement_parser.parser import RequirementParser
from airi.api.approvals import router as approvals_router
from airi.api.development import router as development_router
from airi.api.environments import router as environments_router
from airi.api.errors import ErrorResponse, register_exception_handlers, unexpected_error
from airi.api.executions import router as executions_router
from airi.api.experiments import router as experiments_router
from airi.api.production import router as production_router
from airi.api.refinements import router as refinements_router
from airi.api.reflections import router as reflections_router
from airi.api.registry import router as registry_router
from airi.api.routes import router
from airi.api.temporal import router as temporal_router
from airi.api.tests import router as tests_router
from airi.core.config import Settings
from airi.experiments.fixtures import experiment_fixture
from airi.infrastructure.database import Database
from airi.infrastructure.llm import LLMClient, OpenAICompatibleLLMClient
from airi.infrastructure.query_executor import (
    DisabledQueryExecutor,
    MockQueryExecutor,
    QueryExecutor,
    SparkSQLExecutor,
)
from airi.infrastructure.relation_fixture import relation_fixture
from airi.observability.logging import configure_logging
from airi.production.adapters import adapter_factory
from airi.production.identity import (
    DisabledIdentityProvider,
    TrustedHeaderIdentityProvider,
)
from airi.production.probe import ThriftSparkConnection
from airi.reflection.models import ReflectionPolicy
from airi.skills.enterprise_relation import enterprise_relation_skills
from airi.skills.invoice import invoice_skills
from airi.skills.registry import SkillRegistry
from airi.tools.candidate_sql import GenerateCandidateSQL, candidate_skills
from airi.tools.growth_sql import GenerateGrowthRateSQL
from airi.tools.join_sql import GenerateJoinMetricSQL
from airi.tools.registry import ToolRegistry
from airi.tools.spark_sql import GenerateSparkSQLMetric
from airi.workflows.development.workflow import DevelopmentWorkflow

logger = logging.getLogger("airi.http")


def _default_llm_client(config: Settings) -> LLMClient:
    """Explicit selection only. `demo_mock` never rescues a broken provider config."""
    if config.llm_mode == "demo_mock":
        from airi.infrastructure.demo_llm import DemoRequirementLLM

        return DemoRequirementLLM()
    return OpenAICompatibleLLMClient(config)


def _default_reflection_llm(config: Settings) -> LLMClient:
    if config.llm_mode == "demo_mock":
        from airi.infrastructure.demo_llm import demo_reflection_llm

        return demo_reflection_llm()
    return OpenAICompatibleLLMClient(config)


def create_app(
    settings: Settings | None = None,
    *,
    llm_client: LLMClient | None = None,
    query_executor: QueryExecutor | None = None,
    reflection_llm: LLMClient | None = None,
    reflection_policy: ReflectionPolicy | None = None,
    identity_provider=None,
    production_adapter_factory=None,
    production_connection_factory=None,
) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(config.log_level)
        app.state.database = Database(config)
        try:
            yield
        finally:
            app.state.database.dispose()

    app = FastAPI(
        title=config.app_name,
        version=__version__,
        lifespan=lifespan,
        responses={
            status: {"model": ErrorResponse} for status in (400, 404, 409, 422, 500, 502, 503)
        },
    )
    app.state.settings = config
    if query_executor is not None:
        app.state.query_executor = query_executor
    elif config.environment != "production" and config.effective_execution_mode == "spark_test":
        app.state.query_executor = SparkSQLExecutor(config)
    elif config.environment != "production" and config.effective_execution_mode == "mock":
        if config.demo_fixtures:
            from airi.refinement.fixtures import refinement_fixture

            source, dataset = refinement_fixture()
        else:
            source, dataset = experiment_fixture()
        relation = relation_fixture()
        app.state.query_executor = MockQueryExecutor(
            source,
            dataset_rows=dataset,
            # Both demo scenario families share one local executor: the invoice fact
            # table in `c_db` and the synthetic relationship sources in `demo`.
            relation_tables=relation.tables,
            named_datasets={
                "invoice_risk_sample": dataset,
                "enterprise_relation_sample": relation.labeled_rows,
            },
        )
    else:
        app.state.query_executor = DisabledQueryExecutor()
    app.state.tools = ToolRegistry()
    app.state.skills = SkillRegistry()
    atomic_tool = GenerateSparkSQLMetric()
    app.state.tools.register(atomic_tool)
    app.state.tools.register(GenerateGrowthRateSQL(atomic_tool))
    app.state.tools.register(GenerateCandidateSQL())
    app.state.tools.register(GenerateJoinMetricSQL())
    for skill in invoice_skills():
        app.state.skills.register_shared(skill)
    for skill in enterprise_relation_skills():
        app.state.skills.register_shared(skill)
    for skill in candidate_skills():
        app.state.skills.register(skill)
    client = llm_client if llm_client is not None else _default_llm_client(config)
    app.state.reflection_llm = (
        reflection_llm if reflection_llm is not None else _default_reflection_llm(config)
    )
    app.state.reflection_policy = reflection_policy or ReflectionPolicy()
    app.state.reflection_model = getattr(
        app.state.reflection_llm, "model_identifier", config.llm_model
    )
    app.state.reflection_output_kind = (
        "mock" if app.state.reflection_model.startswith("mock-") else "provider"
    )
    app.state.development_workflow = DevelopmentWorkflow(
        RequirementParser(client), app.state.skills, app.state.tools
    )

    # Phase 7: production identity and adapter boundaries. Both default to
    # inert so a misconfigured production deployment fails closed rather than
    # trusting a client-supplied name or a synthetic runtime.
    if identity_provider is not None:
        app.state.identity_provider = identity_provider
    elif config.production_identity_provider == "trusted_header":
        app.state.identity_provider = TrustedHeaderIdentityProvider(
            config.production_identity_header,
            trust_boundary=config.production_identity_trust_boundary,
            gateway_header=config.production_identity_gateway_header,
            gateway_secret=config.production_identity_gateway_secret.get_secret_value(),
            jwt_secret=config.production_identity_jwt_secret.get_secret_value(),
            issuer=config.production_identity_issuer,
        )
    else:
        app.state.identity_provider = DisabledIdentityProvider()
    runtime_store: dict = {}
    app.state.production_runtime_store = runtime_store
    if production_adapter_factory is not None:
        app.state.production_adapter_factory = production_adapter_factory
    else:
        app.state.production_adapter_factory = adapter_factory(
            config, runtime_store=runtime_store, connection_factory=production_connection_factory
        )
    # Phase 8: the real-runtime connection used for fingerprinting and probing.
    # It is only installed when a production runtime is actually configured;
    # otherwise the evidence routes fail closed on `runtime_connection_not_configured`.
    if production_connection_factory is not None:
        app.state.production_connection_factory = production_connection_factory
    elif config.production_adapter == "spark_production" and config.spark_production_host:
        app.state.production_connection_factory = lambda: ThriftSparkConnection(config)
    else:
        app.state.production_connection_factory = None

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            response = await unexpected_error(request, exc)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response

    register_exception_handlers(app)
    if config.cors_origins:
        # Local Web development only. Origins are pinned in settings; a wildcard
        # is rejected at configuration time.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)
    app.include_router(development_router)
    app.include_router(approvals_router)
    app.include_router(executions_router)
    app.include_router(tests_router)
    app.include_router(environments_router)
    app.include_router(experiments_router)
    app.include_router(reflections_router)
    app.include_router(refinements_router)
    app.include_router(temporal_router)
    app.include_router(registry_router)
    app.include_router(production_router)
    return app


app = create_app()

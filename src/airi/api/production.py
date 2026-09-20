"""Phase 7 production APIs.

Every route resolves an :class:`ActorIdentity` from the configured trusted
identity provider before it touches a production service. There is deliberately
no route that executes production SQL, edits SQL/IR/thresholds, or rolls back
without a human decision.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from airi.infrastructure.database import get_session
from airi.production.identity import ANONYMOUS, ActorIdentity, TrustedIdentityProvider
from airi.production.models import (
    AlertDecision,
    DeploymentReconciliation,
    DeploymentReviewDecision,
    EnvironmentClosureMatrix,
    ExpectationEvaluationRun,
    FeedbackEvent,
    FeedbackRequest,
    FeedbackResearchDecision,
    FeedbackResearchRequest,
    MetricAlert,
    MonitoringExpectation,
    MonitoringExpectationRequest,
    MonitoringExpectationStatus,
    MonitoringIngestResult,
    MonitoringSnapshot,
    MonitoringSnapshotRequest,
    NotificationDelivery,
    ProductionConvergence,
    ProductionDeployment,
    ProductionDeploymentEvidence,
    ProductionDeploymentRequest,
    ProductionDeploymentReview,
    ProductionEnvironmentFingerprint,
    ProductionEnvironmentProfile,
    ProductionEnvironmentRequest,
    ProductionHealth,
    ProductionPreflightResult,
    ProductionRollbackDecision,
    ProductionRollbackRequest,
    ProductionRollbackReview,
    ProductionRuntimeProbe,
    ProductionShadowResult,
    ProductionVerificationReport,
    ReconciliationPlan,
    ReconciliationResult,
    ReconciliationReview,
    ReconciliationReviewCreate,
    ReconciliationReviewDecision,
    ResearchRequestCreate,
    TelemetrySourceHealth,
    TrustedTelemetrySource,
    TrustedTelemetrySourceRequest,
    TrustedTelemetrySourceVerification,
    VerificationGateDecision,
)
from airi.production.service import (
    AlertService,
    DeploymentService,
    EnvironmentService,
    FeedbackService,
    MonitoringService,
    ProductionNotFound,
)

router = APIRouter(prefix="/api/v1", tags=["production"])


def identity_for(request: Request) -> ActorIdentity:
    """The single resolution point. Missing infrastructure means anonymous."""
    provider: TrustedIdentityProvider | None = getattr(request.app.state, "identity_provider", None)
    if provider is None:
        return ANONYMOUS
    return provider.identify(request)


def environment_service(request: Request, session: Session = Depends(get_session)):
    return EnvironmentService(session, request.app.state, request.state.request_id)


def deployment_service(request: Request, session: Session = Depends(get_session)):
    return DeploymentService(session, request.app.state, request.state.request_id)


def monitoring_service(request: Request, session: Session = Depends(get_session)):
    return MonitoringService(session, request.app.state, request.state.request_id)


def alert_service(request: Request, session: Session = Depends(get_session)):
    return AlertService(session, request.app.state, request.state.request_id)


def feedback_service(request: Request, session: Session = Depends(get_session)):
    return FeedbackService(session, request.app.state, request.state.request_id)


# ------------------------------------------------------- production environment


@router.post(
    "/production-environments",
    response_model=ProductionEnvironmentProfile,
    status_code=201,
)
def register_environment(
    payload: ProductionEnvironmentRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(environment_service),
):
    """Declares a production environment. `verified` is decided server-side."""
    return app.register(payload, actor)


@router.get("/production-environments", response_model=list[ProductionEnvironmentProfile])
def list_environments(app=Depends(environment_service)):
    return app.profiles()


@router.get(
    "/production-environments/{environment_id}",
    response_model=ProductionEnvironmentProfile,
)
def get_environment(environment_id: str, app=Depends(environment_service)):
    return app.profile(environment_id)


# --------------------------------------------- Phase 9: environment closure


@router.get(
    "/production-environments/{environment_id}/closure-matrix",
    response_model=EnvironmentClosureMatrix,
)
def get_environment_closure_matrix(environment_id: str, app=Depends(environment_service)):
    """Which NOT VERIFIED items still stand between AIRI and a closed environment.

    A read-only audit surface. Shipping code never turns a row green; only the
    environment producing the evidence does.
    """
    return app.closure_matrix(environment_id)


# ------------------------------------------- Phase 8: real runtime evidence


@router.get(
    "/production-environments/{environment_id}/runtime-probe",
    response_model=ProductionRuntimeProbe,
)
def probe_environment(
    environment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(environment_service),
):
    """Read-only probe. It reports connectivity and identity; it verifies nothing."""
    return app.runtime_probe(environment_id, actor)


@router.post(
    "/production-environments/{environment_id}/fingerprint",
    response_model=ProductionEnvironmentFingerprint,
    status_code=201,
)
def fingerprint_environment(
    environment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(environment_service),
):
    """Observes the live runtime identity so a package can be anchored to a cluster."""
    return app.fingerprint(environment_id, actor)


@router.get(
    "/production-environments/{environment_id}/fingerprints",
    response_model=list[ProductionEnvironmentFingerprint],
)
def list_fingerprints(environment_id: str, app=Depends(environment_service)):
    return app.fingerprints(environment_id)


# ------------------------------------------------------- trusted telemetry


@router.post("/telemetry-sources", response_model=TrustedTelemetrySource, status_code=201)
def register_telemetry_source(
    payload: TrustedTelemetrySourceRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(monitoring_service),
):
    """Registers a producer of runtime telemetry. Registration is not verification."""
    return app.register_telemetry_source(payload, actor)


@router.get("/telemetry-sources", response_model=list[TrustedTelemetrySource])
def list_telemetry_sources(
    environment_id: str | None = Query(default=None),
    app=Depends(monitoring_service),
):
    return app.telemetry_sources(environment_id)


@router.get("/telemetry-sources/{telemetry_source_id}", response_model=TrustedTelemetrySource)
def get_telemetry_source(telemetry_source_id: str, app=Depends(monitoring_service)):
    return app.telemetry_source(telemetry_source_id)


@router.post(
    "/telemetry-sources/{telemetry_source_id}/verify",
    response_model=TrustedTelemetrySource,
)
def verify_telemetry_source(
    telemetry_source_id: str,
    payload: TrustedTelemetrySourceVerification,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(monitoring_service),
):
    """The only path that marks a producer trusted. It records who vouched, and why."""
    return app.verify_telemetry_source(
        telemetry_source_id, actor, note=payload.note, synthetic=payload.synthetic
    )


@router.post(
    "/production-deployments/{deployment_id}/monitoring-expectation",
    response_model=MonitoringExpectation,
    status_code=201,
)
def register_monitoring_expectation(
    deployment_id: str,
    payload: MonitoringExpectationRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(monitoring_service),
):
    """Declares when telemetry should arrive. It creates no schedule and no job."""
    payload = payload.model_copy(update={"deployment_id": deployment_id})
    return app.register_expectation(payload, actor)


@router.get(
    "/production-deployments/{deployment_id}/monitoring-expectation",
    response_model=MonitoringExpectation,
)
def get_monitoring_expectation(deployment_id: str, app=Depends(monitoring_service)):
    expectation = app.expectation(deployment_id)
    if expectation is None:
        raise ProductionNotFound("monitoring_expectation_not_registered")
    return expectation


@router.get(
    "/production-deployments/{deployment_id}/monitoring-expectation/status",
    response_model=MonitoringExpectationStatus,
)
def evaluate_monitoring_expectation(deployment_id: str, app=Depends(monitoring_service)):
    """Evaluates one expectation. It reports; it never opens an alert."""
    return app.evaluate_expectation(deployment_id)


# ------------------------------------- Phase 9: telemetry operational automation


@router.post(
    "/monitoring-expectations/evaluate",
    response_model=ExpectationEvaluationRun,
    status_code=201,
)
def evaluate_monitoring_expectations(
    request: Request,
    deployment_id: str | None = Query(default=None),
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(monitoring_service),
):
    """The call an external scheduler makes. AIRI owns the question, not the ticking.

    Evaluates every declared expectation (or one deployment's), records the run,
    raises one alert per missed window, and offers each alert to the configured
    notification sink. It starts no timer and retries nothing in the background.
    """
    return app.evaluate_expectations(actor, deployment_id=deployment_id)


@router.get(
    "/monitoring-expectations/evaluation-runs/{evaluation_run_id}",
    response_model=ExpectationEvaluationRun,
)
def get_evaluation_run(evaluation_run_id: str, app=Depends(monitoring_service)):
    """Who looked for missing telemetry, when, and what they found."""
    return app.evaluation_run(evaluation_run_id)


@router.get(
    "/telemetry-sources/{telemetry_source_id}/health",
    response_model=TelemetrySourceHealth,
)
def get_telemetry_source_health(telemetry_source_id: str, app=Depends(monitoring_service)):
    """The pipeline's operational state. It is never a verdict about the metric."""
    return app.telemetry_source_health(telemetry_source_id)


# ------------------------------------------------------------- deployment flow


@router.post("/production-deployments", response_model=ProductionDeployment, status_code=201)
def create_deployment(
    payload: ProductionDeploymentRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Starts a governed flow from an approved release and a verified environment."""
    return app.create(payload, actor)


@router.get("/production-deployments", response_model=list[ProductionDeployment])
def list_deployments(
    metric_key: str | None = Query(default=None),
    app=Depends(deployment_service),
):
    return app.deployments(metric_key)


@router.get("/production-deployments/{deployment_id}", response_model=ProductionDeployment)
def get_deployment(deployment_id: str, app=Depends(deployment_service)):
    return app.deployment(deployment_id)


@router.post(
    "/production-deployments/{deployment_id}/preflight",
    response_model=ProductionPreflightResult,
)
def preflight(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Read-only provider preflight. It never deploys and never writes output."""
    deployment = app.preflight(deployment_id, actor)
    return deployment.preflight


@router.post(
    "/production-deployments/{deployment_id}/shadow",
    response_model=ProductionShadowResult,
)
def shadow(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Production shadow: read-only observation that becomes the baseline."""
    deployment = app.shadow(deployment_id, actor)
    return deployment.shadow


@router.post(
    "/production-deployments/{deployment_id}/review",
    response_model=ProductionDeploymentReview,
    status_code=201,
)
def create_deployment_review(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    return app.create_review(deployment_id, actor)


@router.get(
    "/production-deployment-reviews/{deployment_review_id}",
    response_model=ProductionDeploymentReview,
)
def get_deployment_review(deployment_review_id: str, app=Depends(deployment_service)):
    return app.review(deployment_review_id)


@router.post(
    "/production-deployment-reviews/{deployment_review_id}/decision",
    response_model=ProductionDeploymentReview,
)
def decide_deployment_review(
    deployment_review_id: str,
    payload: DeploymentReviewDecision,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Human deployment decision. Gates are re-verified at decision time."""
    return app.decide_review(deployment_review_id, payload, actor)


@router.post(
    "/production-deployments/{deployment_id}/deploy",
    response_model=ProductionDeployment,
)
def deploy(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Provider handover. Claiming production requires a verified runtime identity."""
    return app.deploy(deployment_id, actor)


@router.post(
    "/production-deployments/{deployment_id}/rollback",
    response_model=ProductionRollbackReview,
    status_code=201,
)
def request_rollback(
    deployment_id: str,
    payload: ProductionRollbackRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    return app.request_rollback(deployment_id, payload, actor)


@router.get(
    "/production-rollback-reviews/{rollback_review_id}",
    response_model=ProductionRollbackReview,
)
def get_rollback_review(rollback_review_id: str, app=Depends(deployment_service)):
    return app.rollback_review(rollback_review_id)


@router.post(
    "/production-rollback-reviews/{rollback_review_id}/decision",
    response_model=ProductionRollbackReview,
)
def decide_rollback(
    rollback_review_id: str,
    payload: ProductionRollbackDecision,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Two-phase rollback: provider first, registry pointer second."""
    return app.decide_rollback(rollback_review_id, payload, actor)


@router.get(
    "/production-deployments/{deployment_id}/reconcile",
    response_model=DeploymentReconciliation,
)
def reconcile(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Compares registry and runtime. Detects mismatch; never repairs."""
    return app.reconcile(deployment_id, actor)


@router.get(
    "/production-deployments/{deployment_id}/convergence",
    response_model=ProductionConvergence,
)
def get_deployment_convergence(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Desired vs registry vs runtime - the read-only three-way view.

    It answers "is anything still needed?", which is the question the two-way
    comparison could not ask. It changes nothing: the runtime is re-read, never
    re-aligned.
    """
    return app.convergence(deployment_id, actor)


@router.get(
    "/production-deployments/{deployment_id}/health",
    response_model=ProductionHealth,
)
def get_deployment_health(deployment_id: str, app=Depends(deployment_service)):
    """The workflow status, the runtime state and the convergence state, apart.

    A read-only aggregate. Collapsing these three into one status is what made a
    deployed-but-diverged deployment look healthy.
    """
    return app.health(deployment_id)


@router.get(
    "/production-deployments/{deployment_id}/verification-gate",
    response_model=VerificationGateDecision,
)
def get_verification_gate(
    deployment_id: str,
    request: Request,
    action: str = Query(default="deploy", pattern="^(deploy|rollback|reconciliation)$"),
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """What the gate decides for this action, without performing the action.

    Advisory: it records no decision and gates nothing. The decision that actually
    gates an action is recorded by the action itself.
    """
    return app.verification_gate(deployment_id, actor, action=action)


# --------------------------------- Phase 8: deployment evidence + verification


@router.get(
    "/production-deployments/{deployment_id}/deployment-evidence",
    response_model=list[ProductionDeploymentEvidence],
)
def list_deployment_evidence(deployment_id: str, app=Depends(deployment_service)):
    """What the provider confirmed after each deploy - not what was requested."""
    return app.evidence_records(deployment_id)


@router.get(
    "/production-deployment-evidence/{deployment_evidence_id}",
    response_model=ProductionDeploymentEvidence,
)
def get_deployment_evidence(deployment_evidence_id: str, app=Depends(deployment_service)):
    return app.deployment_evidence(deployment_evidence_id)


@router.post(
    "/production-deployments/{deployment_id}/verification",
    response_model=ProductionVerificationReport,
    status_code=201,
)
def verify_deployment(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Builds a per-section production verification report. It gates nothing."""
    return app.verification(deployment_id, actor)


@router.get(
    "/production-deployments/{deployment_id}/verification-reports",
    response_model=list[ProductionVerificationReport],
)
def list_verification_reports(deployment_id: str, app=Depends(deployment_service)):
    return app.verification_reports(deployment_id)


@router.get(
    "/production-verification-reports/{production_verification_run_id}",
    response_model=ProductionVerificationReport,
)
def get_verification_report(production_verification_run_id: str, app=Depends(deployment_service)):
    return app.verification_report(production_verification_run_id)


# ------------------------------------------------ Phase 8: auditable recovery


@router.post(
    "/production-deployments/{deployment_id}/reconciliation-plans",
    response_model=ReconciliationPlan,
    status_code=201,
)
def plan_reconciliation(
    deployment_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Enumerates the recoveries that exist for a mismatch. Picks none of them."""
    return app.plan_reconciliation(deployment_id, actor)


@router.get(
    "/production-deployments/{deployment_id}/reconciliation-plans",
    response_model=list[ReconciliationPlan],
)
def list_reconciliation_plans(deployment_id: str, app=Depends(deployment_service)):
    return app.reconciliation_plans(deployment_id)


@router.get("/reconciliation-plans/{reconciliation_plan_id}", response_model=ReconciliationPlan)
def get_reconciliation_plan(reconciliation_plan_id: str, app=Depends(deployment_service)):
    return app.reconciliation_plan(reconciliation_plan_id)


@router.post(
    "/reconciliation-plans/{reconciliation_plan_id}/review",
    response_model=ReconciliationReview,
    status_code=201,
)
def create_reconciliation_review(
    reconciliation_plan_id: str,
    payload: ReconciliationReviewCreate,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    return app.create_reconciliation_review(reconciliation_plan_id, payload, actor)


@router.get(
    "/reconciliation-reviews/{reconciliation_review_id}",
    response_model=ReconciliationReview,
)
def get_reconciliation_review(reconciliation_review_id: str, app=Depends(deployment_service)):
    return app.reconciliation_review(reconciliation_review_id)


@router.post(
    "/reconciliation-reviews/{reconciliation_review_id}/decision",
    response_model=ReconciliationResult,
)
def decide_reconciliation_review(
    reconciliation_review_id: str,
    payload: ReconciliationReviewDecision,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Executes the chosen recovery, then re-observes the runtime to confirm it."""
    return app.decide_reconciliation_review(reconciliation_review_id, payload, actor)


@router.post(
    "/reconciliation-plans/{reconciliation_plan_id}/execute",
    response_model=ReconciliationResult,
)
def execute_reconciliation_plan(
    reconciliation_plan_id: str,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(deployment_service),
):
    """Runs an already-approved convergence, idempotently.

    The approval must exist; this route never chooses or approves an action. Running
    it twice is safe: if the target is already satisfied it records
    ``target_state_already_satisfied`` and executes nothing.
    """
    return app.execute_reconciliation_plan(reconciliation_plan_id, actor)


@router.get(
    "/production-deployments/{deployment_id}/reconciliation-results",
    response_model=list[ReconciliationResult],
)
def list_reconciliation_results(deployment_id: str, app=Depends(deployment_service)):
    return app.reconciliation_results(deployment_id)


@router.get(
    "/reconciliation-results/{reconciliation_result_id}",
    response_model=ReconciliationResult,
)
def get_reconciliation_result(reconciliation_result_id: str, app=Depends(deployment_service)):
    return app.reconciliation_result(reconciliation_result_id)


# ----------------------------------------------------------------- monitoring


@router.post(
    "/monitoring-snapshots",
    response_model=MonitoringIngestResult,
    status_code=201,
)
def ingest_snapshot(
    payload: MonitoringSnapshotRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(monitoring_service),
):
    """Ingests an aggregate runtime summary. Opens alerts; never rolls back."""
    snapshot, alerts, created, warnings = app.ingest(payload, actor)
    return MonitoringIngestResult(
        snapshot=snapshot,
        alerts=list(alerts),
        created_alert_ids=list(created),
        warnings=list(warnings),
    )


@router.get(
    "/production-deployments/{deployment_id}/monitoring-snapshots",
    response_model=list[MonitoringSnapshot],
)
def list_snapshots(deployment_id: str, app=Depends(monitoring_service)):
    return app.snapshots(deployment_id)


# --------------------------------------------------------------------- alerts


@router.get("/metric-alerts", response_model=list[MetricAlert])
def list_alerts(
    deployment_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    app=Depends(alert_service),
):
    return app.alerts(deployment_id, status)


@router.get("/metric-alerts/{alert_id}", response_model=MetricAlert)
def get_alert(alert_id: str, app=Depends(alert_service)):
    return app.alert(alert_id)


@router.post("/metric-alerts/{alert_id}/decision", response_model=MetricAlert)
def decide_alert(
    alert_id: str,
    payload: AlertDecision,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(alert_service),
):
    """Alert triage. This API has no rollback action by design."""
    return app.decide(alert_id, payload, actor)


# --------------------------------------- Phase 9: alert delivery, not alert


@router.get("/metric-alerts/{alert_id}/notifications", response_model=list[NotificationDelivery])
def list_alert_notifications(alert_id: str, app=Depends(alert_service)):
    """Every delivery attempt for this alert, across sinks. Never folded into it."""
    return app.notifications(alert_id)


@router.post(
    "/metric-alerts/{alert_id}/notifications",
    response_model=NotificationDelivery,
    status_code=201,
)
def deliver_alert_notification(
    alert_id: str,
    request: Request,
    resend: bool = Query(default=False),
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(alert_service),
):
    """Attempt delivery now. A retry is this explicit call - there is no queue.

    A sink that fails returns a categorised failure instead of raising, because a
    chat platform being unreachable must never undo the alert it was carrying.
    """
    return app.notify(alert_id, actor, resend=resend)


@router.post(
    "/metric-alerts/{alert_id}/feedback",
    response_model=FeedbackEvent,
    status_code=201,
)
def feedback_from_alert(
    alert_id: str,
    request: Request,
    note: str | None = Query(default=None),
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(feedback_service),
):
    return app.record_from_alert(alert_id, note, actor)


# ------------------------------------------------------------------- feedback


@router.post(
    "/metric-feedback-events",
    response_model=FeedbackEvent,
    status_code=201,
)
def record_feedback(
    payload: FeedbackRequest,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(feedback_service),
):
    """Records a production fact. Never an interpretation, never a rewrite."""
    return app.record(payload, actor)


@router.get("/metric-feedback-events", response_model=list[FeedbackEvent])
def list_feedback(
    deployment_id: str | None = Query(default=None),
    app=Depends(feedback_service),
):
    return app.events(deployment_id)


@router.get(
    "/metric-feedback-events/{feedback_event_id}",
    response_model=FeedbackEvent,
)
def get_feedback(feedback_event_id: str, app=Depends(feedback_service)):
    return app.event(feedback_event_id)


@router.post(
    "/metric-feedback-events/{feedback_event_id}/research-requests",
    response_model=FeedbackResearchRequest,
    status_code=201,
)
def request_research(
    feedback_event_id: str,
    payload: ResearchRequestCreate,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(feedback_service),
):
    """A human asks for revalidation. This starts no reflection or experiment."""
    return app.request_research(feedback_event_id, payload, actor)


@router.get(
    "/feedback-research-requests/{research_request_id}",
    response_model=FeedbackResearchRequest,
)
def get_research_request(research_request_id: str, app=Depends(feedback_service)):
    return app.research_request(research_request_id)


@router.post(
    "/feedback-research-requests/{research_request_id}/decision",
    response_model=FeedbackResearchRequest,
)
def decide_research(
    research_request_id: str,
    payload: FeedbackResearchDecision,
    request: Request,
    actor: ActorIdentity = Depends(identity_for),
    app=Depends(feedback_service),
):
    return app.decide_research(research_request_id, payload, actor)

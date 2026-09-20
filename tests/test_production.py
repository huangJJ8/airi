"""Phase 7 production integration, monitoring and feedback-loop tests.

Two layers:

* pure unit tests over identity, adapters, monitoring and package semantics, and
* one full governed chain (registry release -> production deployment) that the
  integration tests share.
"""

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from test_experiments import setup_experiment
from test_phase2 import NAMES, app_for
from test_refinement import refinement_fixture
from test_registry import (
    accept_proposal,
    promotion_review,
    refinement_for,
    register_version,
    release_and_activate,
)
from test_temporal import registered_series

from airi.approvals.persistence import DevelopmentArtifactRow
from airi.core.config import Settings
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.production.adapters import (
    DisabledProductionAdapter,
    MockProductionAdapter,
    ProductionAdapterError,
    SparkProductionAdapter,
    adapter_factory,
)
from airi.production.identity import (
    ACTION_ROLES,
    ANONYMOUS,
    ActorNotAuthorized,
    IdentityNotTrusted,
    IdentityRequired,
    MockIdentityProvider,
    TrustedHeaderIdentityProvider,
    actor,
    authorize,
    require_authenticated,
    require_trusted,
)
from airi.production.models import (
    DeploymentPackage,
    DeploymentReconciliation,
    EmergencyOverride,
    MonitoringDistribution,
    MonitoringDistributionBin,
    MonitoringExpectationRequest,
    MonitoringPolicy,
    ProductionDeployment,
    ProductionDeploymentPolicy,
    ProductionEnvironmentProfile,
    ProductionRollbackPreflight,
    ProductionRuntimeProbe,
    ProductionVerificationPolicy,
    ProductionVerificationReport,
    build_fingerprint,
    deployment_package_hash,
    environment_fingerprint_hash,
)
from airi.production.monitoring import (
    MonitoringAssessor,
    bins_from_values,
    finding_dedup_key,
    recommended_action,
    runtime_psi,
    telemetry_freshness,
)
from airi.production.packaging import artifact_failures, package_failures
from airi.production.persistence import ProductionDeploymentRow
from airi.production.reconciliation import build_reconciliation_plan, convergence_status
from airi.production.verification import build_verification_report
from airi.refinement.fixtures import MockWindowReflectionLLM
from airi.reflection.evidence import decode
from airi.registry.persistence import MetricReleaseEventRow
from airi.registry.service import RegistryService

ADMIN = {"x-airi-actor": "admin"}
DEPLOYER = {"x-airi-actor": "deployer"}
ROLLBACK = {"x-airi-actor": "rollback"}
METRIC_REVIEWER = {"x-airi-actor": "metric"}
RELEASE_REVIEWER = {"x-airi-actor": "release"}
ENVIRONMENT_ID = "prod_synth"
TELEMETRY_SOURCE_ID = "synthetic_monitor"


# ------------------------------------------------------------------- identity


def test_anonymous_cannot_act():
    with pytest.raises(IdentityRequired):
        authorize(ANONYMOUS, "request_production_deployment")
    with pytest.raises(IdentityRequired):
        require_authenticated(ANONYMOUS)


def test_role_matrix_is_enforced():
    deployer = actor("d", ("production_deployer",))
    rollback = actor("r", ("rollback_approver",))
    metric = actor("m", ("metric_reviewer",))
    assert authorize(deployer, "review_production_deployment") is deployer
    with pytest.raises(ActorNotAuthorized):
        authorize(metric, "review_production_deployment")
    with pytest.raises(ActorNotAuthorized):
        authorize(deployer, "approve_production_rollback")
    assert authorize(rollback, "approve_production_rollback") is rollback
    # `admin` satisfies every declared action.
    admin = actor("a", ("admin",))
    for action in ACTION_ROLES:
        assert authorize(admin, action) is admin


def test_unknown_action_is_a_programming_error():
    with pytest.raises(KeyError):
        authorize(actor("a", ("admin",)), "launch_missiles")


# ------------------------------------------------------------------- adapters


def test_disabled_adapter_fails_closed():
    adapter = DisabledProductionAdapter()
    assert adapter.authoritative is False
    assert adapter.runtime_mode == "disabled"
    status = adapter.status("any")
    assert status.runtime_state == "unknown"


def test_spark_production_adapter_fails_closed_without_configuration():
    state = SimpleNamespace(settings=Settings(_env_file=None, environment="test"))
    adapter = SparkProductionAdapter(None, state)
    with pytest.raises(ProductionAdapterError):
        adapter.validate(None)
    assert adapter.status("x").runtime_state == "unknown"


def test_mock_adapter_is_forbidden_in_production():
    config = Settings(_env_file=None, environment="production", production_adapter="mock")
    factory = adapter_factory(config)
    with pytest.raises(ProductionAdapterError):
        factory(None, None, None)


def test_synthetic_production_profile_is_forbidden_in_production():
    """A synthetic production environment may never be declared in production."""
    from airi.production.models import ProductionEnvironmentRequest
    from airi.production.service import EnvironmentService, ProductionError

    state = SimpleNamespace(settings=Settings(_env_file=None, environment="production"))
    service = EnvironmentService(None, state, None)
    payload = ProductionEnvironmentRequest(
        environment_id="prod_synth",
        name="Synthetic production environment",
        environment_kind="production",
        execution_mode="mock",
        deployment_enabled=True,
        synthetic_profile=True,
    )
    with pytest.raises(ProductionError) as excinfo:
        service.register(payload, actor("admin-1", ("admin",)))
    assert excinfo.value.code == "synthetic_production_profile_forbidden"


def test_adapter_factory_returns_uniform_callable():
    state = SimpleNamespace(settings=Settings(_env_file=None, environment="test"))
    for adapter_name, environment in (
        ("disabled", "test"),
        ("disabled", "production"),
        ("mock", "test"),
        ("spark_production", "production"),
    ):
        config = Settings(_env_file=None, environment=environment, production_adapter=adapter_name)
        factory = adapter_factory(config, runtime_store={})
        assert callable(factory)
        instance = factory(None, state, None)
        assert hasattr(instance, "deploy") and hasattr(instance, "observe")


# ----------------------------------------------------------------- monitoring


def test_bins_are_aggregate_and_null_last():
    distribution = bins_from_values([1.0, 2.0, None, 4.0, None], bin_count=4)
    assert distribution.total == 5
    assert distribution.bins[-1].is_null is True
    assert distribution.bins[-1].count == 2
    assert sum(item.count for item in distribution.bins) == 5
    assert bins_from_values([]).total == 0


def test_distribution_rejects_unaligned_counts():
    with pytest.raises(ValidationError):
        MonitoringDistribution(
            bins=[MonitoringDistributionBin(count=1), MonitoringDistributionBin(count=1)],
            total=5,
        )
    with pytest.raises(ValidationError):
        MonitoringDistribution(
            bins=[
                MonitoringDistributionBin(count=1, is_null=True),
                MonitoringDistributionBin(count=1),
            ],
            total=2,
        )


def test_runtime_psi_reuses_the_phase5_binning():
    baseline = MonitoringDistribution(
        bins=[MonitoringDistributionBin(count=50), MonitoringDistributionBin(count=50)],
        total=100,
    )
    identical = MonitoringDistribution(
        bins=[MonitoringDistributionBin(count=50), MonitoringDistributionBin(count=50)],
        total=100,
    )
    value, status = runtime_psi(baseline, identical, 0.0001)
    assert status == "available"
    assert value == pytest.approx(0.0, abs=1e-9)
    shifted = MonitoringDistribution(
        bins=[MonitoringDistributionBin(count=95), MonitoringDistributionBin(count=5)],
        total=100,
    )
    value, status = runtime_psi(baseline, shifted, 0.0001)
    assert status == "available"
    assert value > 0.2
    assert runtime_psi(None, shifted, 0.0001) == (None, "not_available")
    misaligned = MonitoringDistribution(bins=[MonitoringDistributionBin(count=1)], total=1)
    assert runtime_psi(baseline, misaligned, 0.0001) == (None, "baseline_mismatch")


def test_dedup_key_is_stable_and_scoped():
    assert finding_dedup_key("d", "coverage_drop", "warning") == finding_dedup_key(
        "d", "coverage_drop", "warning"
    )
    assert finding_dedup_key("d", "coverage_drop", "warning") != finding_dedup_key(
        "d", "coverage_drop", "critical"
    )
    assert recommended_action("critical") == "consider_rollback"
    assert recommended_action("info") == "observe"


def _assess(**overrides):
    from airi.production.models import (
        MonitoringExecution,
        MonitoringQuality,
        MonitoringSnapshot,
    )

    baseline = overrides.pop("_baseline", None)
    payload = {
        "deployment_id": "d",
        "metric_definition_id": "m",
        "metric_version_id": "v",
        "metric_key": "k",
        "version": "1.0.0",
        "observation_time": datetime(2026, 1, 1, tzinfo=UTC),
        "execution": MonitoringExecution(status="success", duration_ms=10, row_count=10),
        "quality": MonitoringQuality(coverage=0.9, null_rate=0.0, duplicate_rate=0.0),
        "distribution": MonitoringDistribution(),
        "source_system": "s",
        "source_event_id": "e",
        "publisher_actor_id": "a",
        "content_hash": "0" * 64,
    }
    payload.update(overrides)
    snapshot = MonitoringSnapshot(**payload)
    findings, summary, warnings = MonitoringAssessor().assess(
        snapshot=snapshot, baseline=baseline, policy=MonitoringPolicy()
    )
    return findings, summary, warnings


def test_severity_escalation_is_deterministic():
    from airi.production.models import MonitoringBaseline

    baseline = MonitoringBaseline(
        deployment_id="d",
        metric_version_id="v",
        source="production_shadow",
        distribution=MonitoringDistribution(),
        row_count=10,
        coverage=0.9,
        synthetic_data=True,
    )
    # coverage drop 0.9 -> 0.4 (drop 0.5) is more than 2x the 0.05 threshold.
    findings, _, _ = _assess(
        quality={"coverage": 0.4, "null_rate": 0.0, "duplicate_rate": 0.0},
        _baseline=baseline,
    )
    by_type = {item.type: item for item in findings}
    assert "coverage_drop" in by_type
    assert by_type["coverage_drop"].severity == "critical"
    assert by_type["coverage_drop"].evidence["threshold"] == 0.05


# ------------------------------------------------------------ production chain


def _identity_provider():
    return MockIdentityProvider(
        {
            "admin": actor("admin-1", ("admin",)),
            "deployer": actor("deployer-1", ("production_deployer",)),
            "rollback": actor("rollback-1", ("rollback_approver",)),
            "metric": actor("metric-reviewer-1", ("metric_reviewer",)),
            "release": actor("release-reviewer-1", ("release_reviewer",)),
        }
    )


def _adapter_factory(store, *, fail_shadow=False, fail_deploy=False, fail_rollback=False):
    def factory(session, state, request_id=None):
        return MockProductionAdapter(
            session,
            state,
            request_id,
            runtime_store=store,
            fail_shadow=fail_shadow,
            fail_deploy=fail_deploy,
            fail_rollback=fail_rollback,
        )

    return factory


@pytest.fixture
def production_chain(settings):
    """Registry lineage: v1.0.0 and v2.0.0 registered; v2.0.0 active."""
    settings.execution_mode = "mock"
    settings.production_adapter = "mock"
    source, rows = refinement_fixture()
    executor = MockQueryExecutor(source, dataset_rows=rows)
    store: dict = {}
    app = app_for(
        settings,
        NAMES[0],
        executor,
        identity_provider=_identity_provider(),
        production_adapter_factory=_adapter_factory(store),
    )
    app.state.reflection_llm = MockWindowReflectionLLM()
    app.state.reflection_model = MockWindowReflectionLLM.model_identifier
    app.state.reflection_output_kind = "mock"
    with TestClient(app) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        exp = client.post(f"/api/v1/experiments/{spec['experiment_spec_id']}/run").json()
        reflection = client.post(
            "/api/v1/reflections",
            json={"experiment_run_ids": [exp["run"]["experiment_run_id"]]},
        ).json()
        rid = reflection["run"]["reflection_run_id"]
        proposal = client.get(f"/api/v1/reflections/{rid}/proposals").json()[0]
        accept_proposal(client, rid, proposal)
        series = registered_series(client, app, exp, True)
        versions = {}
        for days, expected in ((60, "1.0.0"), (90, "2.0.0")):
            review_id = promotion_review(
                client, refinement_for(client, rid, proposal, days), series
            )
            version = register_version(client, review_id)
            assert version["version"] == expected, version
            versions[days] = version
        registers = {}
        for days, expected_active in ((60, None), (90, "1.0.0")):
            release_id, _ = release_and_activate(client, versions[days], expected_active)
            registers[days] = release_id
        assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"
        yield {
            "client": client,
            "app": app,
            "store": store,
            "versions": versions,
            "releases": registers,
            "metric_key": versions[60]["metric_key"],
        }


def _register_environment(client, *, deployable=True, credentials=ADMIN):
    return client.post(
        "/api/v1/production-environments",
        headers=credentials,
        json={
            "environment_id": ENVIRONMENT_ID,
            "name": "Synthetic production environment",
            "environment_kind": "production" if deployable else "test",
            "execution_mode": "mock",
            "deployment_enabled": deployable,
            "synthetic_profile": True,
        },
    )


def _create_deployment(client, chain, credentials=DEPLOYER):
    return client.post(
        "/api/v1/production-deployments",
        headers=credentials,
        json={
            "release_id": chain["releases"][90],
            "environment_id": ENVIRONMENT_ID,
        },
    )


def _register_telemetry_source(client, *, verify=True, synthetic=True, credentials=ADMIN):
    """A producer must be registered before its telemetry is accepted at all."""
    created = client.post(
        "/api/v1/telemetry-sources",
        headers=credentials,
        json={
            "telemetry_source_id": TELEMETRY_SOURCE_ID,
            "source_system": "synthetic_monitor",
            "environment_id": ENVIRONMENT_ID,
            "auth_identity": "monitor@airi.invalid",
        },
    )
    assert created.status_code == 201, created.text
    if not verify:
        return created
    verified = _verify_telemetry_source(client, synthetic=synthetic)
    assert verified.status_code == 200, verified.text
    return created


def _verify_telemetry_source(client, *, synthetic=True, credentials=ADMIN):
    return client.post(
        f"/api/v1/telemetry-sources/{TELEMETRY_SOURCE_ID}/verify",
        headers=credentials,
        json={
            "note": "operator attested the producer identity out of band",
            "synthetic": synthetic,
        },
    )


def _drive_to_deployed(client, chain, *, adapter=None, telemetry_source=True):
    assert _register_environment(client).status_code == 201
    if telemetry_source:
        _register_telemetry_source(client)
    created = _create_deployment(client, chain)
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_id"]
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/preflight", headers=DEPLOYER
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/shadow", headers=DEPLOYER
        ).status_code
        == 200
    )
    review = client.post(f"/api/v1/production-deployments/{deployment_id}/review", headers=DEPLOYER)
    assert review.status_code == 201, review.text
    decided = client.post(
        f"/api/v1/production-deployment-reviews/{review.json()['deployment_review_id']}/decision",
        headers=DEPLOYER,
        json={"decision": "approved", "comment": "Synthetic production approval"},
    )
    assert decided.status_code == 200, decided.text
    deployed = client.post(
        f"/api/v1/production-deployments/{deployment_id}/deploy", headers=DEPLOYER
    )
    assert deployed.status_code == 200, deployed.text
    return deployment_id


# ------------------------------------------------------------ environment gate


def test_environment_registration_requires_admin(production_chain):
    client = production_chain["client"]
    refused = _register_environment(client, credentials=DEPLOYER)
    assert refused.status_code == 403, refused.text
    anonymous = client.post(
        "/api/v1/production-environments",
        json={
            "environment_id": "prod_anon",
            "name": "n",
            "environment_kind": "production",
            "execution_mode": "mock",
            "deployment_enabled": True,
            "synthetic_profile": True,
        },
    )
    assert anonymous.status_code == 401, anonymous.text


def test_environment_verification_is_decided_server_side(production_chain):
    client = production_chain["client"]
    profile = _register_environment(client).json()
    assert profile["verified"] is True
    assert profile["verification_source"] == "synthetic_profile"
    unverified = client.post(
        "/api/v1/production-environments",
        headers=ADMIN,
        json={
            "environment_id": "prod_unverified",
            "name": "Pending validation",
            "environment_kind": "production",
            "execution_mode": "spark_production",
            "deployment_enabled": True,
        },
    ).json()
    assert unverified["verified"] is False
    assert unverified["verification_source"] == "none"


def test_deployment_requires_a_verified_environment(production_chain):
    client = production_chain["client"]
    anonymous = _create_deployment(client, production_chain, credentials={})
    assert anonymous.status_code == 401, anonymous.text
    unregistered = _create_deployment(client, production_chain)
    assert unregistered.status_code == 404, unregistered.text
    # A registered but non-deployable environment is refused at the gate.
    assert _register_environment(client, deployable=False).status_code == 201
    not_verified = _create_deployment(client, production_chain)
    assert not_verified.status_code == 409, not_verified.text
    assert not_verified.json()["error"]["code"] == "production_environment_not_verified"


# ------------------------------------------------------------------ full chain


def test_full_governed_production_flow(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)

    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    assert deployment["status"] == "deployed"
    assert deployment["production_deployed"] is False
    assert deployment["runtime_identity_verified"] is False
    assert deployment["runtime_mode"] == "mock"
    assert deployment["shadow"]["status"] == "passed"
    assert deployment["monitoring_baseline"]["source"] == "production_shadow"
    assert any("runtime identity" in item for item in deployment["missing_evidence"])
    assert any("mock" in item for item in deployment["missing_evidence"])

    # Reconciliation is a read-only comparison; it never repairs.
    reconcile = client.get(
        f"/api/v1/production-deployments/{deployment_id}/reconcile", headers=ADMIN
    ).json()
    assert reconcile["status"] in ("consistent", "unknown")

    events = [
        item["event_type"] for item in client.get("/api/v1/metrics/invoice_amount/events").json()
    ]
    for expected in (
        "production_preflight_validated",
        "production_shadow_started",
        "production_shadow_validated",
        "production_deployment_approved",
        "production_deployed",
    ):
        assert expected in events, events

    # Environment registration is not metric-scoped, so it is audited under its
    # own reserved scope rather than being forced onto an arbitrary metric.
    session = production_chain["app"].state.database.session_factory()
    try:
        scopes = set(
            session.scalars(
                select(MetricReleaseEventRow.metric_definition_id).where(
                    MetricReleaseEventRow.event_type == "production_environment_registered"
                )
            )
        )
        assert scopes == {"production_environment"}, scopes
    finally:
        session.close()


def test_illegal_transitions_are_rejected(production_chain):
    client = production_chain["client"]
    assert _register_environment(client).status_code == 201
    deployment_id = _create_deployment(client, production_chain).json()["deployment_id"]
    # created -> shadow is not a deployment flow.
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/shadow", headers=DEPLOYER
        ).status_code
        == 409
    )
    # created -> deploy is not a deployment flow.
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/deploy", headers=DEPLOYER
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/preflight", headers=DEPLOYER
        ).status_code
        == 200
    )
    # preflight -> review before a shadow is rejected.
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/review", headers=DEPLOYER
        ).status_code
        == 409
    )


def test_deployment_cannot_be_created_twice_for_one_version(production_chain):
    client = production_chain["client"]
    assert _register_environment(client).status_code == 201
    assert _create_deployment(client, production_chain).status_code == 201
    duplicate = _create_deployment(client, production_chain)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "version_already_in_production_flow"


def test_mock_deploy_never_claims_production_deployed(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    # The honesty validator refuses the opposite claim.
    forged = dict(deployment)
    forged["production_deployed"] = True
    with pytest.raises(ValidationError):
        ProductionDeployment.model_validate(forged)


def test_deploy_policy_is_versioned_and_never_automatic():
    policy = ProductionDeploymentPolicy()
    assert policy.automatic_rollback is False
    assert policy.cutover_mode == "manual"
    assert policy.shadow_write_business_output is False
    assert policy.version == "1.0.0"


# ------------------------------------------------------------ monitoring/alerts


def _snapshot_payload(deployment, **overrides):
    baseline = deployment["monitoring_baseline"]
    distribution = baseline["distribution"]
    total = max(1, distribution["total"])
    payload = {
        "deployment_id": deployment["deployment_id"],
        "telemetry_source_id": TELEMETRY_SOURCE_ID,
        "observation_time": "2026-02-01T00:00:00+00:00",
        "execution": {"status": "success", "duration_ms": 120, "row_count": total},
        "quality": {
            "coverage": baseline.get("coverage"),
            "null_rate": baseline.get("null_rate"),
            "duplicate_rate": baseline.get("duplicate_rate"),
        },
        "distribution": distribution,
        "source_system": "synthetic_monitor",
        "source_event_id": "event-1",
    }
    payload.update(overrides)
    return payload


def test_monitoring_ingest_opens_alerts_and_dedups(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()

    baseline_coverage = deployment["monitoring_baseline"]["coverage"] or 1.0
    payload = _snapshot_payload(deployment)
    payload["quality"]["coverage"] = max(0.0, baseline_coverage - 0.4)

    first = client.post("/api/v1/monitoring-snapshots", headers=RELEASE_REVIEWER, json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["snapshot"]["execution"]["status"] == "success"
    assert body["created_alert_ids"], body
    types = {alert["type"] for alert in body["alerts"]}
    assert "coverage_drop" in types
    alert = next(item for item in body["alerts"] if item["type"] == "coverage_drop")
    assert alert["automatic_action_taken"] is False
    assert alert["recommended_action"] in ("investigate", "consider_rollback")

    # The deployment advanced to monitoring and the pointer never moved.
    assert (
        client.get(f"/api/v1/production-deployments/{deployment_id}").json()["status"]
        == "monitoring"
    )

    second = client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json={**payload, "source_event_id": "event-2"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["created_alert_ids"] == []
    listed = client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    assert len(listed) == len(body["alerts"])
    deduped = next(item for item in listed if item["type"] == "coverage_drop")
    assert deduped["occurrences"] == 2


def test_alert_decision_never_rolls_back(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    payload = _snapshot_payload(deployment)
    payload["execution"] = {"status": "failed", "duration_ms": 10, "row_count": 0}
    ingested = client.post(
        "/api/v1/monitoring-snapshots", headers=RELEASE_REVIEWER, json=payload
    ).json()
    alert = next(item for item in ingested["alerts"] if item["type"] == "execution_failure")
    assert alert["severity"] == "critical"

    decided = client.post(
        f"/api/v1/metric-alerts/{alert['alert_id']}/decision",
        headers=ROLLBACK,
        json={"decision": "acknowledge", "comment": "Investigating with the data team"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "acknowledged"
    # Nothing was executed: the deployment is still monitoring.
    still = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    assert still["status"] == "monitoring"
    assert still["production_deployed"] is False


# ------------------------------------------------------------------- feedback


def test_feedback_is_a_fact_not_an_interpretation(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    response = client.post(
        "/api/v1/metric-feedback-events",
        headers=METRIC_REVIEWER,
        json={
            "deployment_id": deployment_id,
            "type": "data_quality_degradation",
            "severity": "warning",
            "evidence_refs": [{"kind": "deployment", "ref_id": deployment_id}],
            "note": "Upstream invoice feed lost a partition",
        },
    )
    assert response.status_code == 201, response.text
    event = response.json()
    assert event["record_kind"] == "fact"
    assert event["interpretation"] is None
    assert event["automatic_research_started"] is False

    # An unresolvable evidence reference is refused, not silently accepted.
    bad = client.post(
        "/api/v1/metric-feedback-events",
        headers=METRIC_REVIEWER,
        json={
            "deployment_id": deployment_id,
            "type": "runtime_failure",
            "severity": "critical",
            "evidence_refs": [{"kind": "alert", "ref_id": "does-not-exist"}],
        },
    )
    assert bad.status_code == 404, bad.text


def test_research_request_never_starts_reflection(production_chain):
    client = production_chain["client"]
    experiments_before = len(client.get("/api/v1/experiments").json())
    deployment_id = _drive_to_deployed(client, production_chain)
    event = client.post(
        "/api/v1/metric-feedback-events",
        headers=METRIC_REVIEWER,
        json={
            "deployment_id": deployment_id,
            "type": "distribution_drift",
            "severity": "warning",
            "evidence_refs": [{"kind": "deployment", "ref_id": deployment_id}],
        },
    ).json()
    request = client.post(
        f"/api/v1/metric-feedback-events/{event['feedback_event_id']}/research-requests",
        headers=METRIC_REVIEWER,
        json={"request_type": "revalidation", "rationale": "Population moved after cutover"},
    )
    assert request.status_code == 201, request.text
    assert request.json()["automatic_reflection"] is False

    # Only a metric reviewer or admin may decide the research request.
    refused = client.post(
        f"/api/v1/feedback-research-requests/{request.json()['research_request_id']}/decision",
        headers=DEPLOYER,
        json={"decision": "approved", "comment": "nope"},
    )
    assert refused.status_code == 403, refused.text

    decided = client.post(
        f"/api/v1/feedback-research-requests/{request.json()['research_request_id']}/decision",
        headers=METRIC_REVIEWER,
        json={"decision": "approved", "comment": "Open a revalidation"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision"] == "approved"
    # The decision recorded a human intent; it created no experiment or reflection.
    assert len(client.get("/api/v1/experiments").json()) == experiments_before


# ------------------------------------------------------------------- rollback


def test_two_phase_rollback_moves_provider_then_registry(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)

    request = client.post(
        f"/api/v1/production-deployments/{deployment_id}/rollback",
        headers=DEPLOYER,
        json={
            "target_version": "1.0.0",
            "reason": "Synthetic regression observed after cutover",
            "alert_ids": [],
        },
    )
    assert request.status_code == 201, request.text
    review = request.json()
    assert review["automatic"] is False

    refused = client.post(
        f"/api/v1/production-rollback-reviews/{review['rollback_review_id']}/decision",
        headers=DEPLOYER,
        json={"decision": "approved", "comment": "not my role"},
    )
    assert refused.status_code == 403, refused.text

    approved = client.post(
        f"/api/v1/production-rollback-reviews/{review['rollback_review_id']}/decision",
        headers=ROLLBACK,
        json={"decision": "approved", "comment": "Roll back to the stable version"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["production_rollback_status"] == "succeeded"
    assert body["registry_reconciliation_status"] == "reconciled"
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "1.0.0"
    assert (
        client.get(f"/api/v1/production-deployments/{deployment_id}").json()["status"]
        == "rolled_back"
    )


def test_partial_rollback_failure_names_the_reconciliation_state(production_chain):
    client = production_chain["client"]
    chain = production_chain
    chain["app"].state.production_adapter_factory = _adapter_factory(
        chain["store"], fail_rollback=True
    )
    deployment_id = _drive_to_deployed(client, chain)
    request = client.post(
        f"/api/v1/production-deployments/{deployment_id}/rollback",
        headers=DEPLOYER,
        json={"target_version": "1.0.0", "reason": "Provider failure drill"},
    )
    assert request.status_code == 201, request.text
    review_id = request.json()["rollback_review_id"]
    failed = client.post(
        f"/api/v1/production-rollback-reviews/{review_id}/decision",
        headers=ROLLBACK,
        json={"decision": "approved", "comment": "Drill"},
    )
    assert failed.status_code == 409, failed.text
    assert failed.json()["error"]["code"] == "production_rollback_failed"
    body = client.get(f"/api/v1/production-rollback-reviews/{review_id}").json()
    assert body["production_rollback_status"] == "failed"
    assert body["registry_reconciliation_status"] == "skipped"
    # The registry pointer never moved.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"


def test_reconciliation_detects_mismatch_without_repairing(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    # Force the runtime to a different version than the registry pointer.
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    report = client.get(
        f"/api/v1/production-deployments/{deployment_id}/reconcile", headers=ADMIN
    ).json()
    assert report["status"] == "mismatch"
    assert report["automatic_correction"] is False
    assert report["registry_active_version"] == "2.0.0"
    # Detection only: the registry pointer and deployment status are untouched.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"
    mismatch_alerts = [
        item
        for item in client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
        if item["type"] == "deployment_state_mismatch"
    ]
    assert mismatch_alerts and mismatch_alerts[0]["severity"] == "critical"


# --------------------------------------------------------------------- reads


def test_reads_and_not_found(production_chain):
    client = production_chain["client"]
    assert client.get("/api/v1/production-environments").json() == []
    assert client.get("/api/v1/production-environments/missing").status_code == 404
    assert client.get("/api/v1/production-deployments").json() == []
    assert client.get("/api/v1/production-deployments/missing").status_code == 404
    assert client.get("/api/v1/metric-alerts").json() == []
    assert client.get("/api/v1/metric-alerts/missing").status_code == 404
    assert client.get("/api/v1/metric-feedback-events").json() == []
    assert client.get("/api/v1/metric-feedback-events/missing").status_code == 404
    # Phase 8 surfaces are reads too: absent evidence never becomes a claim.
    assert client.get("/api/v1/telemetry-sources").json() == []
    assert client.get("/api/v1/telemetry-sources/missing").status_code == 404
    assert (
        client.get("/api/v1/production-deployments/missing/monitoring-expectation").status_code
        == 404
    )
    assert client.get("/api/v1/reconciliation-plans/missing", headers=ADMIN).status_code == 404
    assert client.get("/api/v1/production-deployment-evidence/missing").status_code == 404
    assert client.get("/api/v1/production-verification-reports/missing").status_code == 404


# ---------------------------------------------------------- package integrity


def _package_context(chain):
    """Drive one deployment, then read its governed package and profile back.

    The package is read through the API and the lineage through the registry, so
    the test proves the two agree rather than trusting a locally-built object.
    """
    client = chain["client"]
    deployment_id = _drive_to_deployed(client, chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    package = decode(DeploymentPackage, deployment["package"])
    profile = decode(
        ProductionEnvironmentProfile,
        client.get(f"/api/v1/production-environments/{ENVIRONMENT_ID}").json(),
    )
    session = chain["app"].state.database.session_factory()
    registry = RegistryService(session, chain["app"].state)
    return package, profile, registry, deployment_id


def test_package_verifies_clean_against_its_lineage(production_chain):
    package, profile, registry, _ = _package_context(production_chain)
    # Nothing was tampered with, so full re-verification is silent.
    assert package_failures(registry, package, profile) == []
    # And the stored hash is a re-derivation from the lineage, never a client value.
    assert deployment_package_hash(package) == package.content_hash


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"sql_hash": "0" * 64}, "package_hash_mismatch"),
        ({"environment_id": "another_env"}, "environment_id_mismatch"),
        ({"metric_version_content_hash": "0" * 64}, "metric_version_changed"),
        ({"artifact_id": "no_such_artifact"}, "release_artifact_not_found"),
    ],
)
def test_package_tampering_is_detected(production_chain, update, expected):
    package, profile, registry, _ = _package_context(production_chain)
    tampered = package.model_copy(update=update)
    assert expected in package_failures(registry, tampered, profile)


def test_package_detects_a_changed_environment_profile(production_chain):
    package, profile, registry, _ = _package_context(production_chain)
    # The environment was redefined after the package was approved.
    changed = profile.model_copy(update={"name": "Renamed after approval"})
    assert "environment_profile_changed" in package_failures(registry, package, changed)


def test_package_detects_a_different_metric_ir_hash(production_chain):
    package, profile, registry, _ = _package_context(production_chain)
    tampered = package.model_copy(update={"metric_ir_hash": "0" * 64})
    failures = package_failures(registry, tampered, profile)
    assert "package_metric_ir_hash_unverifiable" in failures
    assert "package_metric_ir_hash_mismatch" in failures


def test_sql_must_match_the_governed_artifact(production_chain):
    package, _, registry, _ = _package_context(production_chain)
    artifact_row = registry.session.get(DevelopmentArtifactRow, package.artifact_id)
    version = registry.version_by_id(package.metric_version_id)
    assert artifact_failures(artifact_row, version, package) == []

    # SQL that is not the governed artifact's SQL is fatal, even if it hashes.
    injected = package.model_copy(update={"sql": package.sql + "\n-- injected"})
    assert "package_sql_not_from_governed_artifact" in artifact_failures(
        artifact_row, version, injected
    )

    # A sql_hash that does not cover the SQL it ships is fatal.
    lying = package.model_copy(update={"sql_hash": "1" * 64})
    assert "package_sql_hash_mismatch" in artifact_failures(artifact_row, version, lying)


def test_artifact_failures_rederive_every_hash(production_chain):
    package, _, registry, _ = _package_context(production_chain)
    row = registry.session.get(DevelopmentArtifactRow, package.artifact_id)
    version = registry.version_by_id(package.metric_version_id)

    def forged_row(**overrides):
        base = {
            "snapshot": row.snapshot,
            "artifact_hash": row.artifact_hash,
            "metric_ir_hash": row.metric_ir_hash,
            "artifact_id": row.artifact_id,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    # The artifact document no longer hashes to the hash recorded on the row.
    assert "artifact_content_hash_mismatch" in artifact_failures(
        forged_row(artifact_hash="0" * 64), version, None
    )
    # The artifact's metric IR no longer hashes to its recorded hash.
    assert "artifact_metric_ir_hash_mismatch" in artifact_failures(
        forged_row(metric_ir_hash="0" * 64), version, None
    )
    # The version points at a different artifact than the row it was built from.
    assert "version_artifact_hash_mismatch" in artifact_failures(
        forged_row(artifact_hash="0" * 64), version, None
    )
    assert "version_metric_ir_hash_mismatch" in artifact_failures(
        forged_row(metric_ir_hash="0" * 64), version, None
    )
    # An unreadable artifact document fails closed instead of being skipped.
    unreadable = SimpleNamespace(snapshot={"artifact": {"not": "an artifact"}})
    assert artifact_failures(unreadable, version, None) == ["artifact_document_unreadable"]
    # A version whose own metric IR does not hash to its recorded hash is fatal.
    drifted = version.model_copy(update={"metric_ir_hash": "0" * 64})
    assert "metric_ir_hash_mismatch" in artifact_failures(row, drifted, None)


def test_tampered_deployment_package_is_refused_on_read(production_chain):
    """Tampering with the stored package is caught by the read guard, not executed."""
    chain = production_chain
    client = chain["client"]
    package, _, registry, deployment_id = _package_context(chain)
    row = registry.session.get(ProductionDeploymentRow, deployment_id)
    # `sql_hash` is inside the hash surface, so the re-derivation will disagree.
    # A deep copy is required: mutating the nested dict in place would leave the
    # ORM's change detection with nothing to flush.
    forged = copy.deepcopy(row.deployment_json)
    forged["package"]["sql_hash"] = "0" * 64
    row.deployment_json = forged
    registry.session.commit()

    refused = client.get(f"/api/v1/production-deployments/{deployment_id}", headers=ADMIN)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "deployment_package_integrity_error"
    # The forgery is detectable from the recorded package alone, so it can never
    # be mistaken for a legitimate delivery.
    forged_package = decode(DeploymentPackage, forged["package"])
    assert deployment_package_hash(forged_package) != forged_package.content_hash
    assert forged_package.content_hash == package.content_hash


# ==================================================== Phase 8: unit semantics


def test_fingerprint_hash_is_the_anti_swap_anchor():
    """The same cluster facts hash identically; a different cluster never does."""
    here = build_fingerprint(
        environment_id="prod_a",
        cluster_identifier="cluster-a",
        engine_version="3.5.0",
        auth_mode="ldap",
        runtime_user="svc_airi",
    )
    again = build_fingerprint(
        environment_id="prod_a",
        cluster_identifier="cluster-a",
        engine_version="3.5.0",
        auth_mode="ldap",
        runtime_user="svc_airi",
    )
    elsewhere = build_fingerprint(
        environment_id="prod_a",
        cluster_identifier="cluster-b",
        engine_version="3.5.0",
        auth_mode="ldap",
        runtime_user="svc_airi",
    )
    assert here.fingerprint_hash == again.fingerprint_hash
    assert here.fingerprint_hash != elsewhere.fingerprint_hash
    assert here.fingerprint_hash == environment_fingerprint_hash(here)
    # The hash is re-derived, never accepted from a caller.
    forged = here.model_copy(update={"fingerprint_hash": "0" * 64})
    assert environment_fingerprint_hash(forged) != forged.fingerprint_hash


def test_fingerprint_is_only_verified_when_the_runtime_answered():
    reachable = build_fingerprint(environment_id="prod_a", reachable=True, source="runtime_probe")
    unreachable = build_fingerprint(environment_id="prod_a", reachable=False)
    synthetic = build_fingerprint(environment_id="prod_a", reachable=True, source="synthetic")
    assert reachable.verified is True
    assert unreachable.verified is False
    assert synthetic.verified is False


def test_probe_does_not_invent_a_runtime_identity():
    silent = ProductionRuntimeProbe(
        environment_id="prod_a", adapter="spark_production", reachable=True
    )
    asserted = ProductionRuntimeProbe(
        environment_id="prod_a",
        adapter="spark_production",
        reachable=True,
        runtime_identity="svc_airi",
        runtime_identity_source="session",
    )
    assert silent.runtime_identity_verified is False
    assert asserted.runtime_identity_verified is True
    # A NOSASL session may be reachable without authenticating anyone.
    fingerprint = silent.fingerprint(
        environment_id="prod_a", read_only_attested=False, observed_by="admin-1"
    )
    assert fingerprint.runtime_identity_verified is False
    assert fingerprint.auth_mode == "unknown"


def test_telemetry_freshness_keeps_the_two_clocks_apart():
    policy = MonitoringPolicy()
    observed = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    assert telemetry_freshness(observed, datetime(2026, 2, 1, 0, 30, tzinfo=UTC), policy) == (
        "fresh",
        pytest.approx(0.5),
    )
    assert telemetry_freshness(observed, datetime(2026, 2, 1, 6, 0, tzinfo=UTC), policy) == (
        "stale",
        pytest.approx(6.0),
    )
    assert telemetry_freshness(observed, datetime(2026, 2, 3, 0, 0, tzinfo=UTC), policy) == (
        "late",
        pytest.approx(48.0),
    )
    # A snapshot observed long ago and received just now is never "fresh".
    freshness, lag = telemetry_freshness(
        datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC), policy
    )
    assert freshness == "late"
    assert lag > 24


def test_monitoring_expectation_schedules_nothing():
    """An expectation is a declaration, so it carries no trigger of any kind."""
    fields = set(MonitoringExpectationRequest.model_fields)
    assert fields == {
        "deployment_id",
        "expected_interval_hours",
        "grace_hours",
        "telemetry_source_id",
    }
    assert not {"cron", "schedule", "trigger", "run_at", "enabled"} & fields


def test_verification_policy_never_requires_a_manufactured_rollback():
    policy = ProductionVerificationPolicy()
    assert policy.require_rollback_verified is False
    assert policy.require_environment_verified is True
    assert policy.require_runtime_identity is True
    assert policy.require_telemetry_verified is True


def _reconciliation(verdict, *, registry_active="v2", runtime_active="v1"):
    return DeploymentReconciliation(
        deployment_id="d",
        metric_definition_id="m",
        metric_version_id="v2",
        registry_active_version_id=registry_active,
        production_runtime_state="active",
        production_active_version_id=runtime_active,
        status=verdict,
    )


def _plan(deployment, reconciliation, *, authoritative, supports_recovery, lookup):
    return build_reconciliation_plan(
        deployment=deployment,
        reconciliation=reconciliation,
        evidence=None,
        latest_deployment_review_id="review-1",
        rollback_review_ids=[],
        alert_ids=["alert-1"],
        version_lookup=lookup,
        adapter_name="mock",
        adapter_authoritative=authoritative,
        adapter_supports_recovery=supports_recovery,
        created_by="deployer-1",
    )


def _fake_deployment(**overrides):
    """The planning/report surfaces read attributes only, so a light stand-in is enough."""
    base = {
        "deployment_id": "d",
        "metric_definition_id": "m",
        "metric_version_id": "v2",
        "metric_key": "k",
        "version": "2.0.0",
        "release_id": "r",
        "environment_id": "prod_a",
        "environment_fingerprint_hash": None,
        "package": SimpleNamespace(environment_fingerprint_hash=None),
        "runtime_mode": "mock",
        "runtime_identity": None,
        "runtime_identity_verified": False,
        "shadow": None,
        "requested_by": "deployer-1",
        "production_deployed": False,
        "reconciliation": None,
        "synthetic_data": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_reconciliation_plan_never_declares_which_side_is_true():
    version = SimpleNamespace(metric_version_id="v1", version="1.0.0")
    plan = _plan(
        _fake_deployment(),
        _reconciliation("mismatch"),
        authoritative=True,
        supports_recovery=True,
        lookup=lambda version_id: version if version_id == "v1" else None,
    )
    # It offers recoveries and says why; it never asserts a winner.
    assert plan.automatic_correction is False
    assert set(plan.possible_actions) == {
        "registry_to_runtime",
        "runtime_to_registry",
        "manual_investigation",
    }
    assert plan.recommended_action in plan.possible_actions
    assert "not a verdict" in plan.rationale


def test_reconciliation_withholds_a_recovery_it_cannot_execute():
    version = SimpleNamespace(metric_version_id="v1", version="1.0.0")
    # A non-authoritative adapter cannot re-push anything to production.
    non_authoritative = _plan(
        _fake_deployment(),
        _reconciliation("mismatch"),
        authoritative=False,
        supports_recovery=True,
        lookup=lambda version_id: version if version_id == "v1" else None,
    )
    assert "registry_to_runtime" not in non_authoritative.possible_actions
    assert non_authoritative.recommended_action == "runtime_to_registry"

    # A runtime running something the registry never produced cannot be adopted.
    unknown_runtime = _plan(
        _fake_deployment(),
        _reconciliation("mismatch", runtime_active="ghost"),
        authoritative=False,
        supports_recovery=True,
        lookup=lambda version_id: None,
    )
    assert unknown_runtime.possible_actions == ["manual_investigation"]
    assert unknown_runtime.recommended_action == "manual_investigation"

    # An unreadable runtime is never aligned in either direction.
    unknown_state = _plan(
        _fake_deployment(),
        _reconciliation("unknown", runtime_active=None),
        authoritative=True,
        supports_recovery=True,
        lookup=lambda version_id: version,
    )
    assert unknown_state.recommended_action == "manual_investigation"


def test_convergence_is_only_ever_confirmed_by_re_observation():
    assert convergence_status(_reconciliation("consistent")) == "verified"
    assert convergence_status(_reconciliation("mismatch")) == "still_mismatch"
    assert convergence_status(_reconciliation("unknown")) == "not_verified"


def test_verification_report_is_per_section_not_one_boolean():
    report = build_verification_report(
        deployment=_fake_deployment(),
        profile=ProductionEnvironmentProfile(
            environment_id="prod_a",
            name="Synthetic",
            environment_kind="production",
            execution_mode="mock",
            deployment_enabled=True,
            verification_source="synthetic_profile",
            verified=True,
            synthetic_profile=True,
        ),
        fingerprint=None,
        evidence=None,
        telemetry_source=None,
        rollback_review=None,
        adapter_name="mock",
        identity_trusted=False,
        decision_actors_trusted=True,
    )
    by_name = {section.name: section for section in report.sections}
    assert by_name["rollback"].status == "not_verified"
    assert by_name["monitoring_source"].status == "not_verified"
    assert report.production_runtime_verified is False
    assert report.overall != "verified"
    assert set(report.blocking) >= {
        "environment",
        "identity",
        "adapter",
        "deployment",
        "runtime_identity",
        "monitoring_source",
    }
    assert "rollback" in report.not_verified


def test_verification_report_flags_a_swapped_environment():
    """A package approved against cluster A fails against cluster B."""
    package_fingerprint = build_fingerprint(
        environment_id="prod_a", cluster_identifier="cluster-a", reachable=True
    )
    other_fingerprint = build_fingerprint(
        environment_id="prod_a", cluster_identifier="cluster-b", reachable=True
    )
    deployment = _fake_deployment(
        runtime_mode="spark_production",
        environment_fingerprint_hash=package_fingerprint.fingerprint_hash,
        package=SimpleNamespace(environment_fingerprint_hash=package_fingerprint.fingerprint_hash),
    )
    report = build_verification_report(
        deployment=deployment,
        profile=ProductionEnvironmentProfile(
            environment_id="prod_a",
            name="Real",
            environment_kind="production",
            execution_mode="spark_production",
            deployment_enabled=True,
            verification_source="environment_validation_report",
            verified=True,
        ),
        fingerprint=other_fingerprint,
        evidence=None,
        telemetry_source=None,
        rollback_review=None,
        adapter_name="spark_production",
        identity_trusted=True,
        decision_actors_trusted=True,
    )
    section = next(item for item in report.sections if item.name == "environment")
    assert section.status == "failed"
    assert section.evidence["observed"] == other_fingerprint.fingerprint_hash
    assert section.evidence["packaged"] == package_fingerprint.fingerprint_hash
    assert report.overall == "failed"


# --------------------------------------------------------- identity boundary


GATEWAY_BODY = (
    '{"actor_id": "a", "display_name": "A", "auth_source": "trusted_header", "roles": ["admin"]}'
)


def test_a_header_is_untrusted_without_a_real_boundary():
    provider = TrustedHeaderIdentityProvider("x-airi-actor", trust_boundary="none")
    request = SimpleNamespace(headers={"x-airi-actor": GATEWAY_BODY})
    with pytest.raises(IdentityNotTrusted):
        provider.identify(request)
    # Nothing asserted is still anonymous, which fails closed elsewhere.
    assert provider.identify(SimpleNamespace(headers={})).auth_source == "anonymous"


def test_a_gateway_attestation_is_what_makes_the_header_trusted():
    provider = TrustedHeaderIdentityProvider(
        "x-airi-actor", trust_boundary="trusted_gateway", gateway_secret="attested-value"
    )
    good = SimpleNamespace(
        headers={"x-airi-actor": GATEWAY_BODY, "x-airi-gateway-attestation": "attested-value"}
    )
    identity = provider.identify(good)
    assert identity.trusted is True
    assert require_trusted(identity) is identity
    forged = SimpleNamespace(headers={"x-airi-actor": GATEWAY_BODY})
    with pytest.raises(IdentityNotTrusted):
        provider.identify(forged)


def test_a_signed_token_is_trusted_and_an_unsigned_one_is_not():
    import base64
    import hashlib
    import hmac
    import json

    def segment(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    secret = "unit-test-secret"
    claims = {
        "sub": "deployer-1",
        "name": "Deployer",
        "roles": ["production_deployer"],
        "iss": "airi",
    }
    signed_input = f"{segment({'alg': 'HS256', 'typ': 'JWT'})}.{segment(claims)}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), signed_input.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    provider = TrustedHeaderIdentityProvider(
        "x-airi-actor", trust_boundary="signed_jwt", jwt_secret=secret, issuer="airi"
    )
    identity = provider.identify(
        SimpleNamespace(headers={"x-airi-actor": f"{signed_input}.{signature}"})
    )
    assert identity.auth_source == "external_jwt"
    assert identity.trusted is True
    assert identity.holds("production_deployer")

    tampered = TrustedHeaderIdentityProvider(
        "x-airi-actor", trust_boundary="signed_jwt", jwt_secret="another-secret", issuer="airi"
    )
    with pytest.raises(IdentityNotTrusted):
        tampered.identify(SimpleNamespace(headers={"x-airi-actor": f"{signed_input}.{signature}"}))


def test_rollback_preflight_names_missing_evidence():
    preflight = ProductionRollbackPreflight(
        deployment_id="d",
        target_version_id="v1",
        target_version="1.0.0",
        status="passed",
        missing_evidence=["rollback:runtime_supports_rollback"],
    )
    assert preflight.status == "passed"
    assert preflight.missing_evidence


# ==================================================== Phase 8: API semantics


def _event_types(client):
    events = client.get("/api/v1/metrics/invoice_amount/events").json()
    return [item["event_type"] for item in events]


def test_a_synthetic_environment_cannot_be_fingerprinted(production_chain):
    """Observing a runtime is not applicable to a synthetic environment profile."""
    client = production_chain["client"]
    assert _register_environment(client).status_code == 201
    probe = client.get(
        f"/api/v1/production-environments/{ENVIRONMENT_ID}/runtime-probe", headers=ADMIN
    )
    assert probe.status_code == 409, probe.text
    assert probe.json()["error"]["code"] == "production_runtime_probe_not_applicable"
    fingerprint = client.post(
        f"/api/v1/production-environments/{ENVIRONMENT_ID}/fingerprint", headers=ADMIN
    )
    assert fingerprint.status_code == 409, fingerprint.text
    assert fingerprint.json()["error"]["code"] == "environment_fingerprint_not_applicable"
    assert client.get(f"/api/v1/production-environments/{ENVIRONMENT_ID}/fingerprints").json() == []


def test_telemetry_registration_is_not_telemetry_verification(production_chain):
    client = production_chain["client"]
    assert _register_environment(client).status_code == 201
    created = client.post(
        "/api/v1/telemetry-sources",
        headers=ADMIN,
        json={
            "telemetry_source_id": TELEMETRY_SOURCE_ID,
            "source_system": "synthetic_monitor",
            "environment_id": ENVIRONMENT_ID,
            "auth_identity": "monitor@airi.invalid",
        },
    )
    assert created.status_code == 201, created.text
    # Registration alone never marks a producer trusted.
    assert created.json()["verified"] is False
    assert created.json()["synthetic"] is False

    # The only route that can flip it is an admin attestation.
    assert _verify_telemetry_source(client, credentials=DEPLOYER).status_code == 403
    assert (
        client.post(
            f"/api/v1/telemetry-sources/{TELEMETRY_SOURCE_ID}/verify",
            json={"note": "anonymous", "synthetic": True},
        ).status_code
        == 401
    )
    verified = _verify_telemetry_source(client, synthetic=False)
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True
    assert verified.json()["synthetic"] is False
    # The attestation records who vouched, and why.
    assert (
        client.get(f"/api/v1/telemetry-sources/{TELEMETRY_SOURCE_ID}").json()["registered_by"]
        == "admin-1"
    )
    assert client.get(
        f"/api/v1/telemetry-sources?environment_id={ENVIRONMENT_ID}"
    ).json()  # listed for its environment


def test_unregistered_telemetry_is_refused_outright(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    payload = _snapshot_payload(deployment)
    payload["telemetry_source_id"] = "never_registered"
    refused = client.post("/api/v1/monitoring-snapshots", headers=RELEASE_REVIEWER, json=payload)
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "telemetry_source_not_registered"


def test_unverified_telemetry_is_recorded_but_never_trusted(production_chain):
    """Received is not trusted: the snapshot is kept, and it says so."""
    client = production_chain["client"]
    chain = production_chain
    assert _register_environment(client).status_code == 201
    _register_telemetry_source(client, verify=False)
    created = _create_deployment(client, chain)
    assert created.status_code == 201, created.text
    deployment_id = created.json()["deployment_id"]
    for step in ("preflight", "shadow"):
        assert (
            client.post(
                f"/api/v1/production-deployments/{deployment_id}/{step}", headers=DEPLOYER
            ).status_code
            == 200
        )
    review = client.post(
        f"/api/v1/production-deployments/{deployment_id}/review", headers=DEPLOYER
    ).json()
    client.post(
        f"/api/v1/production-deployment-reviews/{review['deployment_review_id']}/decision",
        headers=DEPLOYER,
        json={"decision": "approved", "comment": "approve"},
    )
    assert (
        client.post(
            f"/api/v1/production-deployments/{deployment_id}/deploy", headers=DEPLOYER
        ).status_code
        == 200
    )
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    ingested = client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_snapshot_payload(deployment),
    )
    assert ingested.status_code == 201, ingested.text
    body = ingested.json()
    assert body["snapshot"]["telemetry_trust"] == "unverified"
    assert body["snapshot"]["telemetry_source_id"] == TELEMETRY_SOURCE_ID
    assert "telemetry_source_not_trusted" in body["warnings"]


def test_a_snapshot_is_idempotent_per_source_event(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    payload = _snapshot_payload(deployment)
    first = client.post("/api/v1/monitoring-snapshots", headers=RELEASE_REVIEWER, json=payload)
    assert first.status_code == 201, first.text
    again = client.post("/api/v1/monitoring-snapshots", headers=RELEASE_REVIEWER, json=payload)
    assert again.status_code == 201, again.text
    # The same producer event is one fact, so it is stored exactly once.
    assert (
        again.json()["snapshot"]["monitoring_snapshot_id"]
        == first.json()["snapshot"]["monitoring_snapshot_id"]
    )
    assert "duplicate_telemetry_event_ignored" in again.json()["warnings"]
    listed = client.get(
        f"/api/v1/production-deployments/{deployment_id}/monitoring-snapshots"
    ).json()
    assert len(listed) == 1


def test_freshness_compares_observation_to_receipt(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    fresh = client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_snapshot_payload(
            deployment,
            observation_time=datetime.now(UTC).isoformat(),
            source_event_id="fresh-1",
        ),
    ).json()
    assert fresh["snapshot"]["telemetry_freshness"] == "fresh"
    assert fresh["snapshot"]["observation_lag_hours"] < 1
    # A backdated observation delivered now is stale/late, never current.
    late = client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_snapshot_payload(
            deployment, observation_time="2026-02-01T00:00:00+00:00", source_event_id="late-1"
        ),
    ).json()
    assert late["snapshot"]["telemetry_freshness"] == "late"
    # The two clocks are stored separately and are never conflated.
    assert late["snapshot"]["received_at"] != late["snapshot"]["observation_time"]


def test_an_expectation_reports_overdue_telemetry_without_alerting(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    registered = client.post(
        f"/api/v1/production-deployments/{deployment_id}/monitoring-expectation",
        headers=ADMIN,
        json={
            "deployment_id": deployment_id,
            "expected_interval_hours": 1,
            "grace_hours": 0,
            "telemetry_source_id": TELEMETRY_SOURCE_ID,
        },
    )
    assert registered.status_code == 201, registered.text
    # Declaring an expectation creates no scheduler and no job.
    assert "automatic_scheduling" not in registered.json()

    assert (
        client.post(
            "/api/v1/monitoring-snapshots",
            headers=RELEASE_REVIEWER,
            json=_snapshot_payload(deployment, source_event_id="expectation-1"),
        ).status_code
        == 201
    )
    status = client.get(
        f"/api/v1/production-deployments/{deployment_id}/monitoring-expectation/status"
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "overdue"
    assert status.json()["hours_since_last_observation"] > 0
    # Evaluation is advisory: it opened no alert of its own.
    alert_types = {
        item["type"]
        for item in client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    }
    assert "telemetry_overdue" not in alert_types


def test_deployment_evidence_records_what_the_provider_confirmed(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    evidence = client.get(
        f"/api/v1/production-deployments/{deployment_id}/deployment-evidence"
    ).json()
    assert len(evidence) == 1, evidence
    record = evidence[0]
    assert record["deploy_status"] == "deployed"
    assert record["status_confirmed"] is True
    # A synthetic provider is never allowed to claim a production deployment.
    assert record["production_deployed"] is False
    assert record["authoritative"] is False
    fetched = client.get(
        f"/api/v1/production-deployment-evidence/{record['deployment_evidence_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["evidence_hash"] == record["evidence_hash"]


def test_rollback_records_its_preflight_and_runtime_verification(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    request = client.post(
        f"/api/v1/production-deployments/{deployment_id}/rollback",
        headers=DEPLOYER,
        json={"target_version": "1.0.0", "reason": "Exercise the preflight"},
    )
    assert request.status_code == 201, request.text
    assert request.json()["preflight"]["status"] == "passed"
    assert "production_rollback_preflight_validated" in _event_types(client)

    approved = client.post(
        f"/api/v1/production-rollback-reviews/{request.json()['rollback_review_id']}/decision",
        headers=ROLLBACK,
        json={"decision": "approved", "comment": "approved"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    # §49: verified by re-reading the runtime, not by the provider's own message.
    assert body["runtime_verified"] is True


def test_verification_report_names_every_unverified_section(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    report = client.post(
        f"/api/v1/production-deployments/{deployment_id}/verification", headers=ADMIN
    )
    assert report.status_code == 201, report.text
    body = report.json()
    assert body["overall"] == "partially_verified"
    assert body["production_runtime_verified"] is False
    assert body["production_deployed"] is False
    # A synthetic mock chain verifies nothing real, and the report says which.
    assert set(body["blocking"]) >= {
        "environment",
        "identity",
        "adapter",
        "deployment",
        "runtime_identity",
        "monitoring_source",
    }
    assert "rollback" in body["not_verified"]
    assert "production_verification_recorded" in _event_types(client)
    listed = client.get(
        f"/api/v1/production-deployments/{deployment_id}/verification-reports"
    ).json()
    assert len(listed) == 1
    fetched = client.get(
        f"/api/v1/production-verification-reports/{body['production_verification_run_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["report_hash"] == body["report_hash"]


def test_reconciliation_plan_needs_a_real_mismatch(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    refused = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "production_reconciliation_not_required"


def test_reconciliation_executes_then_verifies_convergence(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    # Force drift: the runtime reports the older version the registry knows about.
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    )
    assert plan.status_code == 201, plan.text
    body = plan.json()
    assert body["verdict"] == "mismatch"
    assert body["automatic_correction"] is False
    assert "manual_investigation" in body["possible_actions"]

    review = client.post(
        f"/api/v1/reconciliation-plans/{body['reconciliation_plan_id']}/review",
        headers=ADMIN,
        json={"comment": "investigate and recover"},
    )
    assert review.status_code == 201, review.text
    decided = client.post(
        f"/api/v1/reconciliation-reviews/{review.json()['reconciliation_review_id']}/decision",
        headers=ADMIN,
        json={"decision": "align_registry_to_runtime", "comment": "runtime is the fact"},
    )
    assert decided.status_code == 200, decided.text
    result = decided.json()
    assert result["execution_status"] == "executed"
    assert result["convergence_status"] == "verified"
    assert result["final_status"] == "reconciliation_completed"
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "1.0.0"
    events = _event_types(client)
    for expected in (
        "reconciliation_planned",
        "reconciliation_approved",
        "reconciliation_executed",
        "reconciliation_verified",
    ):
        assert expected in events, events


def test_reconciliation_may_end_with_the_mismatch_open(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    ).json()
    review = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/review",
        headers=ADMIN,
        json={"comment": "look first"},
    ).json()
    decided = client.post(
        f"/api/v1/reconciliation-reviews/{review['reconciliation_review_id']}/decision",
        headers=ADMIN,
        json={"decision": "keep_mismatch_for_investigation", "comment": "escalate"},
    )
    assert decided.status_code == 200, decided.text
    result = decided.json()
    assert result["execution_status"] == "skipped"
    assert result["final_status"] == "reconciliation_not_executed"
    # Nothing was repaired: the registry pointer is untouched.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"
    assert "reconciliation_executed" not in _event_types(client)


def test_reconciliation_refuses_an_action_it_never_offered(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    # A runtime running a version the registry never produced.
    chain["store"][deployment_id] = {"metric_version_id": "ghost_version", "state": "active"}
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    ).json()
    assert plan["possible_actions"] == ["manual_investigation"]
    review = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/review",
        headers=ADMIN,
        json={"comment": "choose"},
    ).json()
    refused = client.post(
        f"/api/v1/reconciliation-reviews/{review['reconciliation_review_id']}/decision",
        headers=ADMIN,
        json={"decision": "align_registry_to_runtime", "comment": "adopt the runtime"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "reconciliation_action_not_available"
    # The registry never adopted a version it did not produce.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"


def test_a_deployed_trust_boundary_refuses_a_local_identity(production_chain):
    """Once a boundary exists, an injected identity is no longer good enough."""
    client = production_chain["client"]
    chain = production_chain
    assert _register_environment(client).status_code == 201
    _register_telemetry_source(client)
    deployment_id = _create_deployment(client, chain).json()["deployment_id"]
    for step in ("preflight", "shadow"):
        assert (
            client.post(
                f"/api/v1/production-deployments/{deployment_id}/{step}", headers=DEPLOYER
            ).status_code
            == 200
        )
    review = client.post(
        f"/api/v1/production-deployments/{deployment_id}/review", headers=DEPLOYER
    ).json()
    chain["app"].state.settings.production_identity_trust_boundary = "trusted_gateway"
    try:
        refused = client.post(
            f"/api/v1/production-deployment-reviews/{review['deployment_review_id']}/decision",
            headers=DEPLOYER,
            json={"decision": "approved", "comment": "local identity"},
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "identity_not_trusted"
    finally:
        chain["app"].state.settings.production_identity_trust_boundary = "none"
    # With the boundary removed the local identity is usable again.
    assert (
        client.post(
            f"/api/v1/production-deployment-reviews/{review['deployment_review_id']}/decision",
            headers=DEPLOYER,
            json={"decision": "approved", "comment": "approve"},
        ).status_code
        == 200
    )


# ================================== Phase 9: operational convergence (9A-9E)
#
# Three states stay three states:
#
#   workflow status   - where the governed flow is
#   runtime state     - what the provider reports
#   convergence state - how the desired, the registry and the runtime relate
#
# and a convergence whose target is already met is a recorded success, never a
# conflict.


# ---------------------------------------------------- 9A desired vs observed


def test_three_way_state_is_exact_only_when_a_desired_state_exists():
    from airi.production.convergence import three_way_state

    # Without a desired state the goal degrades to "the registry and the runtime
    # agree", which is all Phase 8 ever asked. Phase 9 removes that degradation.
    assert three_way_state(
        runtime_state="active",
        desired_version_id=None,
        registry_version_id="v2",
        runtime_version_id="v2",
    ) == ("converged", True)
    # With one, all three must name the same version.
    assert three_way_state(
        runtime_state="active",
        desired_version_id="v1",
        registry_version_id="v2",
        runtime_version_id="v2",
    ) == ("mismatch", False)
    assert three_way_state(
        runtime_state="active",
        desired_version_id="v1",
        registry_version_id="v1",
        runtime_version_id="v1",
    ) == ("converged", True)


def test_an_unreadable_runtime_is_never_compared():
    from airi.production.convergence import observed_runtime_state

    # `status="unknown"` records "the runtime never answered". The raw field still
    # reading "active" is then stale, not evidence.
    assert observed_runtime_state(_reconciliation("unknown")) == "unknown"
    assert observed_runtime_state(_reconciliation("mismatch")) == "active"


def test_no_op_is_a_first_class_convergence_action():
    from airi.production.convergence import available_actions, recommend

    actions = available_actions(
        state="converged",
        registry_version_id="v2",
        runtime_version_id="v2",
        desired_version_id="v2",
        deployment_version_id="v2",
        runtime_version_known=True,
        adapter_authoritative=True,
        adapter_supports_recovery=True,
    )
    # Not an empty list: "nothing needed doing" is a proven governance outcome.
    assert actions == ["no_op"]
    action, rationale = recommend(
        state="converged",
        registry_to_runtime_available=False,
        runtime_to_registry_available=False,
    )
    assert action == "no_op"
    assert "Nothing needs to be executed" in rationale


def test_a_conflict_is_explained_rather_than_raised():
    from airi.production.convergence import diagnose_conflict

    already = diagnose_conflict(expected="v1", actual="v2", desired="v2")
    assert already.category == "target_already_satisfied"
    assert already.requires_manual_review is False
    assert already.safe_to_retry is False

    stale = diagnose_conflict(expected="v2", actual="v2", desired="v1")
    assert stale.category == "stale_expected_state"
    assert stale.safe_to_retry is True

    external = diagnose_conflict(expected="v2", actual="v9", desired="v1")
    assert external.category == "unexpected_external_change"
    assert external.requires_manual_review is True

    unreadable = diagnose_conflict(
        expected="v2", actual=None, desired="v1", runtime_state="unknown"
    )
    assert unreadable.category == "runtime_unknown"
    assert unreadable.safe_to_retry is False


def test_convergence_verdict_separates_ran_from_already_there():
    from airi.production.convergence import convergence_verdict

    assert convergence_verdict(state="converged", executed=False) == "already_converged"
    assert convergence_verdict(state="converged", executed=True) == "converged"
    assert convergence_verdict(state="mismatch", executed=False) == "manual_intervention_required"
    assert convergence_verdict(state="mismatch", executed=True) == "still_mismatch"
    assert convergence_verdict(state="unknown", executed=True) == "not_verified"


def test_the_convergence_view_reports_the_three_states_separately():
    from airi.production.convergence import build_convergence

    version = SimpleNamespace(metric_version_id="v1", version="1.0.0")
    convergence = build_convergence(
        deployment=_fake_deployment(),
        reconciliation=_reconciliation("mismatch"),
        desired=None,
        version_lookup=lambda version_id: version if version_id == "v1" else None,
        adapter_authoritative=True,
        adapter_supports_recovery=True,
    )
    assert convergence.convergence_state == "mismatch"
    assert convergence.registry_active_version_id == "v2"
    assert convergence.runtime_active_version_id == "v1"
    assert convergence.automatic_correction is False


# --------------------------------------------------------- 9A the rollback fix


def test_a_rollback_whose_target_was_already_reached_is_not_a_conflict(production_chain):
    """§8: the Phase 8 regression, and the reason 9A exists.

    A human reconciliation moves the registry pointer onto the rollback target
    before the rollback is approved. The pointer move then *cannot* apply - not
    because anything is broken, but because the goal is already met. Phase 8
    reported a conflict; Phase 9 reports a no-op with a verified outcome.
    """
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)

    # The rollback is requested first, so the review records from=v2 to=v1.
    requested = client.post(
        f"/api/v1/production-deployments/{deployment_id}/rollback",
        headers=DEPLOYER,
        json={"target_version": "1.0.0", "reason": "Exercise the rollback fix"},
    )
    assert requested.status_code == 201, requested.text
    rollback_review_id = requested.json()["rollback_review_id"]
    assert requested.json()["to_version_id"] == chain["versions"][60]["metric_version_id"]

    # The runtime is then observed reporting the rollback target, and a human
    # reconciliation adopts it into the registry - "the runtime is the fact".
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    ).json()
    assert plan["verdict"] == "mismatch"
    review = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/review",
        headers=ADMIN,
        json={"comment": "adopt the runtime"},
    ).json()
    adopted = client.post(
        f"/api/v1/reconciliation-reviews/{review['reconciliation_review_id']}/decision",
        headers=ADMIN,
        json={"decision": "align_registry_to_runtime", "comment": "runtime is the fact"},
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["convergence_status"] == "verified"
    # The pointer has already moved onto the rollback target.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "1.0.0"

    # Now the rollback runs. Its compare-and-set has nothing left to change.
    approved = client.post(
        f"/api/v1/production-rollback-reviews/{rollback_review_id}/decision",
        headers=ROLLBACK,
        json={"decision": "approved", "comment": "execute"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["registry_reconciliation_status"] == "already_satisfied"
    assert body["no_action_required"] is True
    assert body["production_rollback_status"] == "succeeded"
    assert body["conflict_diagnosis"]["category"] == "target_already_satisfied"
    assert body["conflict_diagnosis"]["requires_manual_review"] is False
    # The outcome is recorded as a proven no-op rather than inferred from success.
    assert "convergence_already_satisfied" in _event_types(client)


def test_an_approved_rollback_establishes_the_desired_state(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    requested = client.post(
        f"/api/v1/production-deployments/{deployment_id}/rollback",
        headers=DEPLOYER,
        json={"target_version": "1.0.0", "reason": "Roll back"},
    ).json()
    assert (
        client.post(
            f"/api/v1/production-rollback-reviews/{requested['rollback_review_id']}/decision",
            headers=ROLLBACK,
            json={"decision": "approved", "comment": "go"},
        ).status_code
        == 200
    )
    convergence = client.get(
        f"/api/v1/production-deployments/{deployment_id}/convergence", headers=ADMIN
    )
    assert convergence.status_code == 200, convergence.text
    body = convergence.json()
    assert body["desired_source"] == "rollback"
    assert body["desired_version"] == "1.0.0"
    assert body["runtime_state"] == "active"
    assert body["convergence_state"] == "converged"
    assert body["target_already_satisfied"] is True
    assert body["possible_actions"] == ["no_op"]
    assert body["recommended_action"] == "no_op"
    assert body["automatic_correction"] is False
    assert "desired_runtime_state_established" in _event_types(client)


def test_the_convergence_view_never_repairs_anything(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    body = client.get(
        f"/api/v1/production-deployments/{deployment_id}/convergence", headers=ADMIN
    ).json()
    assert body["convergence_state"] == "mismatch"
    assert body["conflict"]["category"] == "unexpected_external_change"
    assert body["possible_actions"]
    # Reading it did not move the pointer.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"
    # It also needs an authenticated actor: it re-reads the runtime.
    assert (
        client.get(f"/api/v1/production-deployments/{deployment_id}/convergence").status_code == 401
    )


def test_executing_a_converged_plan_is_idempotent(production_chain):
    """§16: running an approved convergence twice is safe by construction."""
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    ).json()
    review = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/review",
        headers=ADMIN,
        json={"comment": "recover"},
    ).json()
    first = client.post(
        f"/api/v1/reconciliation-reviews/{review['reconciliation_review_id']}/decision",
        headers=ADMIN,
        json={"decision": "align_registry_to_runtime", "comment": "runtime is the fact"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["convergence_verdict"] == "converged"

    # The plan is approved, so executing it is allowed - and now a no-op.
    again = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/execute", headers=ADMIN
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["execution_status"] == "skipped"
    assert body["convergence_status"] == "verified"
    assert body["final_status"] == "already_converged"
    assert body["target_already_satisfied"] is True
    assert body["skipped_reason"] == "target_state_already_satisfied"
    assert body["convergence_action"] == "no_op"
    assert body["convergence_verdict"] == "already_converged"


def test_executing_an_unapproved_plan_is_refused(production_chain):
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    plan = client.post(
        f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans", headers=ADMIN
    ).json()
    refused = client.post(
        f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/execute", headers=ADMIN
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "reconciliation_review_required"
    # The pointer never moved.
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"


# ----------------------------------------- 9B telemetry operational automation


def _evaluate_expectations(client, *, deployment_id=None, credentials=ADMIN):
    url = "/api/v1/monitoring-expectations/evaluate"
    if deployment_id:
        url = f"{url}?deployment_id={deployment_id}"
    return client.post(url, headers=credentials)


def _declare_expectation(client, deployment_id, *, interval=1, grace=0):
    return client.post(
        f"/api/v1/production-deployments/{deployment_id}/monitoring-expectation",
        headers=ADMIN,
        json={
            "deployment_id": deployment_id,
            "expected_interval_hours": interval,
            "grace_hours": grace,
            "telemetry_source_id": TELEMETRY_SOURCE_ID,
        },
    )


def _recent_snapshot_payload(deployment, *, hours_ago=2, **overrides):
    """A snapshot whose transport is deliberately not the story.

    `_snapshot_payload` observes in February, so ingesting it also raises the
    *transport* warning `telemetry_arrived_late` - useful when that is the point,
    and noise when the point is the missed window. Two hours of lag is enough for
    a 1-hour expectation to be overdue while staying inside the stale bound, so
    the only alert a Phase 9 expectation test sees is the one it asked for.
    """
    observed = datetime.now(UTC) - timedelta(hours=hours_ago)
    return _snapshot_payload(deployment, observation_time=observed.isoformat(), **overrides)


def test_an_evaluation_run_records_that_somebody_checked(production_chain):
    """§23: "nobody checked" and "we checked and it was fine" must not look alike."""
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id).status_code == 201
    run = _evaluate_expectations(client, deployment_id=deployment_id)
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["expectation_count"] == 1
    assert body["evaluated_by"] == "admin-1"
    assert body["deployment_ids"] == [deployment_id]
    # No reading has arrived and the first window has not closed: the expectation
    # has never been met, but the producer is not yet silent.
    assert body["statuses"][0]["status"] == "no_snapshot"
    assert body["statuses"][0]["findings"] == []
    assert body["telemetry_missing_count"] == 0

    fetched = client.get(
        f"/api/v1/monitoring-expectations/evaluation-runs/{body['evaluation_run_id']}"
    )
    assert fetched.status_code == 200
    # The stored run reads back as the run that was recorded, not as a new verdict.
    assert fetched.json()["evaluation_run_id"] == body["evaluation_run_id"]
    assert fetched.json()["statuses"] == body["statuses"]
    assert fetched.json()["telemetry_missing_count"] == 0
    assert "expectation_evaluated" in _event_types(client)
    # The run is attributable: an anonymous caller may not create one.
    assert _evaluate_expectations(client, credentials={}).status_code == 401


def test_the_evaluate_endpoint_is_the_only_place_an_alert_is_decided(production_chain):
    """§24: evaluating reports; a separate explicit run decides an alert."""
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id, interval=1, grace=0).status_code == 201
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    assert (
        client.post(
            "/api/v1/monitoring-snapshots",
            headers=RELEASE_REVIEWER,
            json=_recent_snapshot_payload(deployment, source_event_id="phase9-missing-1"),
        ).status_code
        == 201
    )
    status = client.get(
        f"/api/v1/production-deployments/{deployment_id}/monitoring-expectation/status"
    ).json()
    assert status["status"] == "overdue"
    assert status["findings"] == ["telemetry_missing"]
    # Reading the status opened nothing: evaluation is advisory.
    assert client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json() == []

    run = _evaluate_expectations(client, deployment_id=deployment_id).json()
    assert run["telemetry_missing_count"] == 1
    assert run["created_alert_ids"]
    alerts = client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    assert [item["type"] for item in alerts] == ["missing_monitoring_data"]
    assert alerts[0]["severity"] == "critical"
    assert "telemetry_missing_detected" in _event_types(client)


def test_re_evaluating_the_same_window_does_not_open_a_second_alert(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id, interval=1, grace=0).status_code == 201
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_recent_snapshot_payload(deployment, source_event_id="phase9-dedup-1"),
    )
    first = _evaluate_expectations(client, deployment_id=deployment_id).json()
    second = _evaluate_expectations(client, deployment_id=deployment_id).json()
    # The same missed window is the same alert, counted twice.
    assert first["created_alert_ids"] == second["created_alert_ids"]
    assert first["statuses"][0]["window_key"] == second["statuses"][0]["window_key"]
    alerts = client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    assert len(alerts) == 1
    assert alerts[0]["occurrences"] == 2


def test_missing_and_late_are_separate_findings():
    from airi.production.expectations import evaluate_expectation

    expectation = SimpleNamespace(
        deployment_id="d",
        monitoring_expectation_id="e",
        metric_definition_id="m",
        metric_version_id="v",
        environment_id="prod_a",
        telemetry_source_id="s",
        expected_interval_hours=2,
        grace_hours=1,
        created_at=datetime(2026, 4, 30, tzinfo=UTC),
    )

    # A reading arrived and it is stale: the pipeline is slow, not silent. The
    # window it arrived in is still open, so "late" is the whole of the story.
    late = evaluate_expectation(
        expectation=expectation,
        last_snapshot=SimpleNamespace(
            observation_time=datetime(2026, 4, 30, 22, tzinfo=UTC),
            telemetry_freshness="late",
        ),
        now=datetime(2026, 4, 30, 23, tzinfo=UTC),
    )
    assert late.status == "satisfied"
    assert late.findings == ["telemetry_late"]
    assert late.window_key

    # Inside the grace window: the next reading is due, nothing is overdue yet,
    # and crossing the deadline is never a single step from fine to critical.
    due_soon = evaluate_expectation(
        expectation=expectation,
        last_snapshot=SimpleNamespace(
            observation_time=datetime(2026, 4, 30, 23, tzinfo=UTC),
            telemetry_freshness="fresh",
        ),
        now=datetime(2026, 5, 1, 1, 30, tzinfo=UTC),
    )
    assert due_soon.status == "due_soon"
    assert due_soon.findings == []

    # The window closed behind an old reading that was itself honest. The producer
    # is now silent about a *window*, and nothing here says the reading was late:
    # the two findings describe two different operational problems, so a window
    # closure never implies a slow pipeline and vice versa.
    missing = evaluate_expectation(
        expectation=expectation,
        last_snapshot=SimpleNamespace(
            observation_time=datetime(2026, 4, 30, 10, tzinfo=UTC),
            telemetry_freshness="fresh",
        ),
        now=datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert missing.status == "overdue"
    assert missing.findings == ["telemetry_missing"]

    # A declaration that has never been met is neither of those. It is a window
    # with no reading at all, and calling it "satisfied" would claim a delivery
    # that never happened.
    never = evaluate_expectation(
        expectation=expectation,
        last_snapshot=None,
        now=datetime(2026, 4, 30, 2, 30, tzinfo=UTC),
    )
    assert never.status == "no_snapshot"
    assert never.findings == []


def test_a_silent_producer_and_a_stale_one_are_named_differently(production_chain):
    """§29: a broken transport never explains a metric, and this says so."""
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id).status_code == 201
    health = client.get(f"/api/v1/telemetry-sources/{TELEMETRY_SOURCE_ID}/health")
    assert health.status_code == 200, health.text
    body = health.json()
    # The fixture attests the producer as synthetic, so it is verified - and the
    # synthetic flag is reported rather than hidden behind `verified`.
    assert body["verified"] is True
    assert body["synthetic"] is True
    assert body["snapshot_count"] == 0
    assert body["status"] == "silent"
    assert deployment_id in body["silent_deployment_ids"]
    assert body["overdue_deployment_ids"] == []
    # Silence is a claim about a pipeline, and never about the metric it feeds.
    assert body["metric_health_implied"] is False

    # A reading arrives, but the window it belongs to has already closed. The
    # producer is no longer silent, and the pipeline is named for the problem it
    # actually has - a delivery that missed its declared window - rather than for
    # a metric it cannot explain either way.
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_recent_snapshot_payload(deployment, source_event_id="phase9-health-1"),
    )
    after = client.get(f"/api/v1/telemetry-sources/{TELEMETRY_SOURCE_ID}/health").json()
    assert after["snapshot_count"] == 1
    assert after["status"] == "degraded"
    assert after["silent_deployment_ids"] == []
    assert deployment_id in after["overdue_deployment_ids"]
    assert after["latest_observation_time"] is not None
    assert after["metric_health_implied"] is False

    assert client.get("/api/v1/telemetry-sources/missing/health").status_code == 404


# --------------------------------------------- 9B alert delivery is not alert


def _alert_id_for(client, deployment_id):
    alerts = client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    return alerts[0]["alert_id"]


def test_an_alert_and_a_notification_are_two_different_things(production_chain):
    """§32/§34: a chat platform being down must never undo an alert."""
    from airi.production.notifications import MockNotificationSink

    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["app"].state.notification_sink = MockNotificationSink()
    assert _declare_expectation(client, deployment_id, interval=1, grace=0).status_code == 201
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_recent_snapshot_payload(deployment, source_event_id="phase9-notify-1"),
    )
    _evaluate_expectations(client, deployment_id=deployment_id)
    alert_id = _alert_id_for(client, deployment_id)
    deliveries = client.get(f"/api/v1/metric-alerts/{alert_id}/notifications").json()
    assert [item["status"] for item in deliveries] == ["delivered"]
    assert deliveries[0]["sink"] == "mock"
    assert deliveries[0]["provider_message_id"]
    assert deliveries[0]["replayed"] is False
    assert "notification_attempted" in _event_types(client)

    # A second attempt would be a duplicate send, so the earlier delivery is
    # returned and marked as the replay it is: no second row, no second message.
    replay = client.post(f"/api/v1/metric-alerts/{alert_id}/notifications", headers=ADMIN)
    assert replay.status_code == 201, replay.text
    assert replay.json()["replayed"] is True
    assert len(client.get(f"/api/v1/metric-alerts/{alert_id}/notifications").json()) == 1

    # An explicit resend is a deliberate second call, so it does deliver again.
    resent = client.post(
        f"/api/v1/metric-alerts/{alert_id}/notifications?resend=true", headers=ADMIN
    )
    assert resent.status_code == 201, resent.text
    assert resent.json()["replayed"] is False
    assert len(client.get(f"/api/v1/metric-alerts/{alert_id}/notifications").json()) == 2
    # Delivering is an action, so an anonymous caller cannot trigger one.
    assert client.post(f"/api/v1/metric-alerts/{alert_id}/notifications").status_code == 401


def test_a_failing_sink_is_recorded_and_never_raised(production_chain):
    from airi.production.notifications import MockNotificationSink

    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["app"].state.notification_sink = MockNotificationSink(fail=True)
    assert _declare_expectation(client, deployment_id, interval=1, grace=0).status_code == 201
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_recent_snapshot_payload(deployment, source_event_id="phase9-notify-fail-1"),
    )
    run = _evaluate_expectations(client, deployment_id=deployment_id)
    # The alert survived the delivery failure entirely.
    assert run.status_code == 201, run.text
    alerts = client.get(f"/api/v1/metric-alerts?deployment_id={deployment_id}").json()
    assert [item["type"] for item in alerts] == ["missing_monitoring_data"]
    deliveries = client.get(f"/api/v1/metric-alerts/{alerts[0]['alert_id']}/notifications").json()
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["failure_category"] == "provider_rejected"
    # A failed delivery never marks the alert resolved.
    assert alerts[0]["status"] == "open"


def test_the_default_sink_is_disabled_and_still_records_the_attempt(production_chain):
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id, interval=1, grace=0).status_code == 201
    deployment = client.get(f"/api/v1/production-deployments/{deployment_id}").json()
    client.post(
        "/api/v1/monitoring-snapshots",
        headers=RELEASE_REVIEWER,
        json=_recent_snapshot_payload(deployment, source_event_id="phase9-notify-off-1"),
    )
    _evaluate_expectations(client, deployment_id=deployment_id)
    alert_id = _alert_id_for(client, deployment_id)
    deliveries = client.get(f"/api/v1/metric-alerts/{alert_id}/notifications").json()
    # Nobody is notified, and the attempt is recorded as exactly that.
    assert [item["status"] for item in deliveries] == ["disabled"]
    assert deliveries[0]["sink"] == "disabled"


# --------------------------------------------- 9C verification enforcement


def _synthetic_profile():
    return ProductionEnvironmentProfile(
        environment_id="prod_a",
        name="Synthetic",
        environment_kind="production",
        execution_mode="mock",
        deployment_enabled=True,
        verification_source="synthetic_profile",
        verified=True,
        synthetic_profile=True,
    )


def _real_profile():
    return ProductionEnvironmentProfile(
        environment_id="prod_real",
        name="Real production",
        environment_kind="production",
        execution_mode="spark_production",
        deployment_enabled=True,
        verification_source="environment_validation_report",
        verified=True,
        synthetic_profile=False,
    )


def _report(*verified_sections):
    from airi.production.models import VerificationSection

    sections = [
        VerificationSection(name=name, status="verified", detail="verified")
        for name in verified_sections
    ]
    return ProductionVerificationReport(
        deployment_id="d",
        metric_definition_id="m",
        metric_version_id="v",
        environment_id="prod_real",
        sections=sections,
        overall="verified",
        report_hash="0" * 64,
    )


def test_enforcement_mode_is_resolved_per_environment_not_per_policy():
    from airi.production.gate import resolve_enforcement_mode

    # A synthetic environment cannot produce real evidence, so enforcing there
    # would only ever teach people to bypass it (§38).
    assert resolve_enforcement_mode(profile=_synthetic_profile(), configured="auto") == (
        "report_only"
    )
    assert resolve_enforcement_mode(profile=_real_profile(), configured="auto") == "enforced"
    # An operator may still pin either one.
    assert resolve_enforcement_mode(profile=_real_profile(), configured="report_only") == (
        "report_only"
    )
    assert resolve_enforcement_mode(profile=_synthetic_profile(), configured="enforced") == (
        "enforced"
    )


def test_the_gate_is_action_aware():
    """§42: a rollback must not be blocked by the pipeline it is rescuing."""
    from airi.production.gate import evaluate_verification_gate

    # Environment, identity and adapter are verified; runtime identity and shadow
    # are not. A deploy needs all five; a rollback needs three.
    common = {
        "deployment_id": "d",
        "environment_id": "prod_real",
        "environment_kind": "production",
        "profile": _real_profile(),
        "report": _report("environment", "identity", "adapter"),
        "capabilities": {
            "rollback_capability": True,
            "recovery_capability": True,
            "authoritative_adapter": True,
        },
        "integrity_failures": [],
        "actor_authenticated": True,
        "actor_trusted": True,
        "trust_boundary_configured": True,
    }
    deploy = evaluate_verification_gate(action="deploy", **common)
    assert deploy.result == "blocked"
    assert set(deploy.missing_requirements) == {"runtime_identity", "shadow"}

    rollback = evaluate_verification_gate(action="rollback", **common)
    assert rollback.result == "allowed"
    # A first deploy has no production telemetry yet, and an emergency rollback
    # must not be gated on the pipeline it is trying to rescue.
    assert "monitoring_source" not in rollback.required_sections

    reconciliation = evaluate_verification_gate(action="reconciliation", **common)
    assert reconciliation.result == "blocked"
    assert reconciliation.missing_requirements == ["runtime_identity"]


def test_the_gate_records_and_does_not_block_a_synthetic_environment():
    from airi.production.gate import evaluate_verification_gate

    decision = evaluate_verification_gate(
        action="deploy",
        deployment_id="d",
        environment_id="prod_a",
        environment_kind="production",
        profile=_synthetic_profile(),
        report=None,
        capabilities={},
        integrity_failures=[],
        actor_authenticated=True,
        actor_trusted=False,
        trust_boundary_configured=False,
    )
    assert decision.mode == "report_only"
    assert decision.result == "allowed"
    # The gaps are still named, so the decision is auditable rather than silent.
    assert set(decision.missing_requirements) >= {"environment", "identity", "adapter"}


def test_integrity_and_authentication_outrank_every_override():
    from airi.production.gate import evaluate_verification_gate

    override = EmergencyOverride(
        action="deploy",
        reason="incident",
        requested_by="admin-1",
        approved_by="admin-1",
        waives=["shadow", "runtime_identity"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    decision = evaluate_verification_gate(
        action="deploy",
        deployment_id="d",
        environment_id="prod_real",
        environment_kind="production",
        profile=_real_profile(),
        report=_report("environment", "identity", "adapter"),
        capabilities={},
        integrity_failures=["package_integrity"],
        actor_authenticated=True,
        actor_trusted=True,
        trust_boundary_configured=True,
        override=override,
    )
    assert decision.result == "blocked"
    assert decision.integrity_requirements == ["package_integrity"]
    assert "no emergency override may waive" in decision.detail.lower()

    # The same override does waive the missing *evidence*.
    waived = evaluate_verification_gate(
        action="deploy",
        deployment_id="d",
        environment_id="prod_real",
        environment_kind="production",
        profile=_real_profile(),
        report=_report("environment", "identity", "adapter"),
        capabilities={},
        integrity_failures=[],
        actor_authenticated=True,
        actor_trusted=True,
        trust_boundary_configured=True,
        override=override,
    )
    assert waived.result == "allowed_with_override"
    assert waived.override_id == override.emergency_override_id


def test_an_unauthenticated_actor_can_never_be_waived():
    from airi.production.gate import evaluate_verification_gate

    override = EmergencyOverride(
        action="deploy",
        reason="incident",
        requested_by="admin-1",
        approved_by="admin-1",
        waives=["shadow", "runtime_identity", "environment", "identity", "adapter"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    decision = evaluate_verification_gate(
        action="deploy",
        deployment_id="d",
        environment_id="prod_real",
        environment_kind="production",
        profile=_real_profile(),
        report=_report("environment", "identity", "adapter"),
        capabilities={},
        integrity_failures=[],
        actor_authenticated=False,
        actor_trusted=False,
        trust_boundary_configured=True,
        override=override,
    )
    assert decision.result == "blocked"
    assert decision.integrity_requirements == ["actor_authentication"]


def test_an_override_waives_evidence_but_never_protected_requirements():
    from airi.production.convergence import PROTECTED_REQUIREMENTS

    with pytest.raises(ValidationError):
        EmergencyOverride(
            action="deploy",
            reason="incident",
            requested_by="admin-1",
            approved_by="admin-1",
            waives=["shadow", "actor_authentication"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    # The refusal is per requirement, not a single hard-coded name.
    for name in PROTECTED_REQUIREMENTS:
        with pytest.raises(ValidationError):
            EmergencyOverride(
                action="rollback",
                reason="incident",
                requested_by="admin-1",
                approved_by="admin-1",
                waives=[name],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
    override = EmergencyOverride(
        action="deploy",
        reason="incident",
        requested_by="admin-1",
        approved_by="admin-1",
        waives=["shadow"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert override.waives == ["shadow"]
    assert override.active_at(datetime.now(UTC)) is True
    # It is time-boxed: an expired override grants nothing.
    assert override.active_at(datetime.now(UTC) + timedelta(hours=2)) is False


def test_the_gate_is_reported_before_the_provider_is_called(production_chain):
    """§47: gate -> action, not action -> gate."""
    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    gate = client.get(
        f"/api/v1/production-deployments/{deployment_id}/verification-gate?action=deploy",
        headers=ADMIN,
    )
    assert gate.status_code == 200, gate.text
    body = gate.json()
    # A synthetic environment records and does not block.
    assert body["mode"] == "report_only"
    assert body["result"] == "allowed"
    assert body["required_sections"] == [
        "environment",
        "identity",
        "adapter",
        "runtime_identity",
        "shadow",
    ]
    assert "package_hash" in body["evidence_refs"]
    assert body["action"] == "deploy"
    # Reading it is advisory and needs an authenticated actor.
    assert (
        client.get(f"/api/v1/production-deployments/{deployment_id}/verification-gate").status_code
        == 401
    )
    # The deploy that really happened recorded its own decision and event.
    assert "verification_gate_passed" in _event_types(client)


# ----------------------------------------------- 9D three states, never collapsed


def test_workflow_runtime_and_convergence_states_stay_apart(production_chain):
    from airi.production.convergence import health_of
    from airi.production.models import ProductionHealth

    client = production_chain["client"]
    chain = production_chain
    deployment_id = _drive_to_deployed(client, chain)
    chain["store"][deployment_id] = {
        "metric_version_id": chain["versions"][60]["metric_version_id"],
        "state": "active",
    }
    health = client.get(f"/api/v1/production-deployments/{deployment_id}/health")
    assert health.status_code == 200, health.text
    body = health.json()
    # The workflow says the deployment succeeded; the runtime disagrees with the
    # desired state. Both facts are reported, in separate fields.
    assert body["workflow_status"] == "deployed"
    assert body["runtime_state"] == "active"
    assert body["convergence_state"] == "mismatch"
    assert body["health"] == "degraded"
    assert body["reasons"]
    # An aggregate view. It authorises nothing by itself.
    assert body["drives_execution"] is False
    assert ProductionHealth.model_fields["drives_execution"].default is False

    assert health_of(
        workflow_status="deployed", runtime_state="active", convergence="converged"
    ) == ("healthy", [])
    assert (
        health_of(workflow_status="failed", runtime_state="unknown", convergence="unknown")[0]
        == "unknown"
    )


def test_the_three_state_enums_are_distinct_types():
    """Collapsing them into one status enum is the mistake Phase 9 undoes."""
    from typing import get_args

    from airi.production.models import ConvergenceState, DeploymentStatus, RuntimeState

    deployment = set(get_args(DeploymentStatus))
    runtime = set(get_args(RuntimeState))
    convergence = set(get_args(ConvergenceState))

    # A convergence verdict has its own vocabulary. It says nothing about where a
    # deployment sits in its workflow, and nothing about what the runtime reports;
    # it is derived from the comparison, not copied from either.
    assert convergence == {"converged", "mismatch", "unknown"}
    assert "converged" not in deployment
    assert "converged" not in runtime

    # The lifecycle and the runtime are free to share a *word* while meaning
    # different things - `failed` is an attempt that failed versus a runtime that
    # reports failure. Sharing a word is precisely why they must stay two types.
    assert {"failed", "rolled_back"} <= deployment
    assert {"failed", "rolled_back"} <= runtime

    # And each vocabulary holds states the other has no business having.
    assert {"preflight_validated", "pending_deployment_review"} <= deployment
    assert not {"preflight_validated", "pending_deployment_review"} & runtime
    assert "shadow" in runtime
    assert "shadow" not in deployment


# ---------------------------------------------------------- 9E closure matrix


def test_the_closure_matrix_never_turns_a_row_green_for_free(production_chain):
    """§54: only the environment producing evidence closes an item."""
    client = production_chain["client"]
    deployment_id = _drive_to_deployed(client, production_chain)
    assert _declare_expectation(client, deployment_id).status_code == 201
    body = client.get(f"/api/v1/production-environments/{ENVIRONMENT_ID}/closure-matrix").json()
    by_name = {item["name"]: item for item in body["entries"]}
    assert by_name["environment_profile"]["status"] == "verified"
    assert by_name["real_production_deployment"]["status"] == "not_verified"
    assert by_name["identity_trust_boundary"]["status"] == "not_verified"
    assert by_name["trusted_telemetry_source"]["status"] == "not_verified"
    assert by_name["monitoring_expectation"]["status"] == "verified"
    assert by_name["notification_sink"]["status"] == "not_verified"
    assert by_name["environment_fingerprint"]["status"] == "not_verified"
    # Shipping the code closed nothing, and the open items are named.
    assert body["production_closed"] is False
    assert body["not_verified_count"] >= 4
    assert "identity_trust_boundary" in body["open_items"]
    assert body["environment_kind"] == "production"
    assert body["blocked_count"] == 0


def test_a_blocked_item_is_named_rather_than_counted_as_verified(production_chain):
    """§54: an item that cannot be attempted yet is `blocked`, never green."""
    client = production_chain["client"]
    # An environment nobody registered has no matrix at all.
    assert client.get("/api/v1/production-environments/missing/closure-matrix").status_code == 404

    # A second environment with nothing in it. Telemetry trust cannot even be
    # attempted here, and "cannot be attempted" is not "not verified yet".
    assert (
        client.post(
            "/api/v1/production-environments",
            headers=ADMIN,
            json={
                "environment_id": "prod_empty",
                "name": "Empty synthetic environment",
                "environment_kind": "production",
                "execution_mode": "mock",
                "deployment_enabled": True,
                "synthetic_profile": True,
            },
        ).status_code
        == 201
    )
    body = client.get("/api/v1/production-environments/prod_empty/closure-matrix").json()
    by_name = {item["name"]: item for item in body["entries"]}
    assert by_name["environment_profile"]["status"] == "verified"
    assert by_name["trusted_telemetry_source"]["status"] == "blocked"
    assert by_name["monitoring_expectation"]["status"] == "not_verified"
    assert by_name["authoritative_adapter"]["status"] == "not_applicable"
    assert body["blocked_count"] >= 1
    assert body["production_closed"] is False
    assert "trusted_telemetry_source" in body["open_items"]
    assert body["environment_kind"] == "production"


# ----------------------------------------------------- audit vocabulary (both)


def test_every_phase9_audit_event_exists_in_both_event_tables():
    """Phase 8 lesson: the deployment event table is not the only one indexed."""
    from typing import get_args

    from airi.production.models import DeploymentEventType
    from airi.registry.models import ReleaseEventType

    expected = {
        "desired_runtime_state_established",
        "convergence_already_satisfied",
        "convergence_conflict_diagnosed",
        "expectation_evaluated",
        "telemetry_missing_detected",
        "notification_attempted",
        "verification_gate_blocked",
        "verification_gate_passed",
    }
    assert expected <= set(get_args(DeploymentEventType))
    assert expected <= set(get_args(ReleaseEventType))

import copy

import pytest
from pydantic import ValidationError
from test_refinement import approve_candidate, baseline_ir
from test_refinement import refinement_app as refinement_fixture
from test_temporal import registered_series

from airi.metric_ir.models import Aggregation, FilterCondition
from airi.registry.models import (
    MetricVersionRequest,
    ReleasePolicy,
    ReleaseRequest,
    ShadowComparisonResult,
    ShadowReference,
    StagingValidationResult,
)
from airi.registry.release import ReleaseDiagnostics
from airi.registry.shadow import ShadowComparator, metric_values
from airi.registry.versioning import MetricFamilyKey, VersionChangeClassifier, semantic_diff

refinement_app = refinement_fixture


def reference(role, name):
    return ShadowReference(
        role=role,
        label=name,
        artifact_id="a" * 36,
        artifact_hash="0" * 64,
        metric_name=name,
    )


def rows(metric_name, values):
    return [{"entity_id": key, metric_name: value} for key, value in values.items()]


# --------------------------------------------------------------------- gates


def accept_proposal(client, rid, proposal):
    response = client.post(
        f"/api/v1/reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
        json={
            "decision": "accepted_for_investigation",
            "reviewer": "fixture",
            "comment": "Synthetic research",
        },
    )
    assert response.status_code == 200, response.text


def refinement_for(client, rid, proposal, days):
    """Drive the Phase 4 loop to an explicit further-validation decision."""
    result = client.post(
        "/api/v1/refinements",
        json={
            "reflection_run_id": rid,
            "proposal_id": proposal["proposal_id"],
            "parameter_selection": {"window_days": days},
        },
    )
    assert result.status_code == 201, result.text
    report = result.json()
    approve_candidate(client, report["candidate"])
    refinement_id = report["run"]["refinement_run_id"]
    evaluated = client.post(f"/api/v1/refinements/{refinement_id}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["run"]["status"] == "completed", evaluated.json()
    decided = client.post(
        f"/api/v1/refinements/{refinement_id}/decision",
        json={
            "decision": "accept_candidate_for_further_validation",
            "reviewer": "fixture",
            "comment": "Synthetic research",
        },
    )
    assert decided.status_code == 200, decided.text
    return refinement_id


def promotion_review(client, refinement_id, series_id, decision="approved_for_versioning"):
    pending = client.post(
        "/api/v1/temporal-validations",
        json={"source_refinement_run_id": refinement_id, "series_id": series_id},
    )
    assert pending.status_code == 201, pending.text
    run_id = pending.json()["spec"]["temporal_validation_run_id"]
    for artifact in pending.json()["spec"]["slice_artifacts"]:
        approve_candidate(client, artifact)
    response = client.post(f"/api/v1/temporal-validations/{run_id}/run")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["failure_category"] is None, report
    assert report["promotion_eligibility"] == "eligible_for_review", report["diagnostics"]
    review = client.post("/api/v1/promotion-reviews", json={"temporal_validation_run_id": run_id})
    assert review.status_code == 201, review.text
    review_id = review.json()["promotion_review_id"]
    decided = client.post(
        f"/api/v1/promotion-reviews/{review_id}/decision",
        json={
            "decision": decision,
            "reviewer": "fixture",
            "comment": "Synthetic governance demonstration",
        },
    )
    assert decided.status_code == 200, decided.text
    return review_id


def register_version(client, review_id):
    response = client.post("/api/v1/metric-versions", json={"promotion_review_id": review_id})
    assert response.status_code == 201, response.text
    return response.json()


def release_and_activate(client, version, expected_active_version):
    created = client.post(
        "/api/v1/releases",
        json={"metric_version_id": version["metric_version_id"], "target_environment": "staging"},
    )
    assert created.status_code == 201, created.text
    release_id = created.json()["release_id"]
    validated = client.post(f"/api/v1/releases/{release_id}/validate")
    assert validated.status_code == 200, validated.text
    report = validated.json()
    assert report["release_eligibility"] == "eligible_for_release_review", report["findings"]
    review = client.post(f"/api/v1/releases/{release_id}/review")
    assert review.status_code == 201, review.text
    decided = client.post(
        f"/api/v1/release-reviews/{review.json()['release_review_id']}/decision",
        json={"decision": "approved", "reviewer": "fixture", "comment": "Synthetic release"},
    )
    assert decided.status_code == 200, decided.text
    activated = client.post(
        f"/api/v1/releases/{release_id}/activate",
        json={"expected_active_version": expected_active_version},
    )
    assert activated.status_code == 200, activated.text
    return release_id, report


@pytest.fixture
def series_id(refinement_app):
    client, app, _, _, original = refinement_app
    return registered_series(client, app, original, True)


@pytest.mark.parametrize("decision", ["rejected", "need_more_evidence"])
def test_metric_version_requires_approved_promotion(refinement_app, series_id, decision):
    """A promotion review that did not approve versioning can never create a version."""
    client, _, rid, proposal, _ = refinement_app
    accept_proposal(client, rid, proposal)
    review_id = promotion_review(
        client, refinement_for(client, rid, proposal, 60), series_id, decision
    )
    response = client.post("/api/v1/metric-versions", json={"promotion_review_id": review_id})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "metric_version_not_authorized"
    assert client.get("/api/v1/metrics").json() == []


def test_metric_version_requires_existing_review(client):
    response = client.post(
        "/api/v1/metric-versions", json={"promotion_review_id": "00000000-0000-0000-0000-0000"}
    )
    assert response.status_code == 404


# ------------------------------------------------------------ version semantics


@pytest.mark.parametrize(
    "case,kind",
    [
        ("identical", "patch"),
        ("display", "patch"),
        ("dimensions", "minor"),
        ("window", "major"),
        ("aggregation", "major"),
        ("entity", "major"),
        ("filters", "major"),
        ("source", "major"),
    ],
)
def test_version_change_classifier(case, kind):
    base = baseline_ir()
    candidate = base
    if case == "display":
        candidate = base.model_copy(update={"description": "Reworded description"})
    elif case == "dimensions":
        candidate = base.model_copy(update={"dimensions": ["invoice_type"]})
    elif case == "window":
        candidate = base.model_copy(update={"window": base.window.model_copy(update={"size": 60})})
    elif case == "aggregation":
        candidate = base.model_copy(
            update={"aggregation": Aggregation(function="count", field=None)}
        )
    elif case == "entity":
        candidate = base.model_copy(update={"entity_key": "buyer_tax_no"})
    elif case == "filters":
        candidate = base.model_copy(
            update={"filters": [FilterCondition(field="invoice_amt", operator="gt", value=0)]}
        )
    elif case == "source":
        candidate = base.model_copy(
            update={"source": type("S", (), {"catalog": None, "database": "other", "table": "t"})()}
        )
    changed, unchanged = semantic_diff(base, candidate)
    assert VersionChangeClassifier().classify(changed) == kind
    if case == "identical":
        assert changed == [] and "entity_key" in unchanged


def test_metric_family_key():
    family = MetricFamilyKey()
    assert family.derive("invoice_amount_30d") == "invoice_amount"
    assert family.derive("invoice_amount_90d") == "invoice_amount"
    assert family.derive("invoice_count") == "invoice_count"
    assert family.derive("30d") == "30d"
    assert family.display_name("invoice_amount") == "invoice amount"


# --------------------------------------------------------------- shadow units


def test_shadow_metric_values_and_duplicates():
    values, duplicates = metric_values(
        [
            {"entity_id": "E1", "invoice_amount_60d": "10"},
            {"entity_id": "E1", "invoice_amount_60d": "11"},
            {"entity_id": "E2", "invoice_amount_60d": None},
            {"entity_id": "E3", "invoice_amount_60d": "not-a-number"},
        ],
        "invoice_amount_60d",
    )
    assert duplicates == 1
    assert values["E2"] is None and values["E3"] is None and str(values["E1"]) == "10"


@pytest.mark.parametrize(
    "case,status,expect",
    [
        ("identical", "passed", {"value_equal": 5, "difference_rate": 0.0}),
        ("distribution", "passed_with_warnings", {"value_changed": 5}),
        ("new_null", "failed", {"new_null": 5}),
        ("resolved_null", "passed", {"resolved_null": 5}),
        ("entity_loss", "failed", {"baseline_only": 1}),
        ("no_match", "inconclusive", {"matched_entities": 0}),
    ],
)
def test_shadow_comparator(case, status, expect):
    baseline_values = {f"E{i}": str(i + 1) for i in range(4)}
    baseline_values["E9"] = "1"
    if case == "identical":
        candidate_values = dict(baseline_values)
    elif case == "distribution":
        candidate_values = {key: str(int(value) + 100) for key, value in baseline_values.items()}
    elif case == "new_null":
        candidate_values = {key: None for key in baseline_values}
    elif case == "resolved_null":
        baseline_values = {key: None for key in baseline_values}
        candidate_values = {f"E{i}": str(i + 1) for i in range(4)}
        candidate_values["E9"] = "1"
    elif case == "entity_loss":
        candidate_values = {f"E{i}": str(i + 1) for i in range(4)}
    else:
        candidate_values = {"Z1": "1"}
    baseline, candidate = (
        reference("lineage_baseline_artifact", "invoice_amount_30d"),
        reference("candidate_metric_version", "invoice_amount_60d"),
    )
    result = ShadowComparator().compare(
        baseline=baseline,
        candidate=candidate,
        baseline_rows=rows("invoice_amount_30d", baseline_values),
        candidate_rows=rows("invoice_amount_60d", candidate_values),
        dataset_snapshot_id="snapshot",
        anchor_time=baseline_ir_anchor(),
        policy=ReleasePolicy(),
    )
    assert result.status == status, result.warnings
    for key, value in expect.items():
        assert getattr(result, key) == value, (key, getattr(result, key))
    # A changed value is expected evidence, never an error on its own.
    if case == "distribution":
        assert result.distribution_delta["p90_relative_shift"] > 0


def baseline_ir_anchor():
    from datetime import datetime

    return datetime.fromisoformat("2026-09-09T00:00:00+08:00")


def staging(status="passed", **overrides):
    data = {
        "status": status,
        "execution_mode": "mock",
        "target_environment": "staging",
        "artifact_id": "a" * 36,
        "artifact_hash": "0" * 64,
        "metric_ir_hash": "1" * 64,
        "execution_run_id": "b" * 36,
        "failure_category": None,
        "row_count": 200,
        "truncated": False,
        "schema_ok": True,
        "coverage": 1.0,
        "null_rate": 0.0,
        "non_null_count": 200,
        "warnings": [],
    }
    return StagingValidationResult(**(data | overrides))


def shadow_result(status, warnings=None, **overrides):
    base = {
        "status": status,
        "baseline": reference("lineage_baseline_artifact", "invoice_amount_30d"),
        "candidate": reference("candidate_metric_version", "invoice_amount_60d"),
        "dataset_snapshot_id": "snapshot",
        "anchor_time": baseline_ir_anchor(),
        "sample_count": 200,
        "matched_entities": 200,
        "baseline_only": 0,
        "candidate_only": 0,
        "value_equal": 200,
        "value_changed": 0,
        "new_null": 0,
        "resolved_null": 0,
        "difference_rate": 0.0,
        "entity_loss_fraction": 0.0,
        "new_null_fraction": 0.0,
        "distribution_delta": {},
        "warnings": warnings or [],
    }
    return ShadowComparisonResult(**(base | overrides))


@pytest.mark.parametrize(
    "case,staging_kwargs,shadow_kwargs,eligibility,status,finding",
    [
        ("clean", {}, {"status": "passed"}, "eligible_for_release_review", "passed", None),
        (
            "execution_failed",
            {"status": "failed"},
            None,
            "not_eligible",
            "failed",
            "staging_execution_failed",
        ),
        (
            "schema_invalid",
            {"schema_ok": False},
            {"status": "passed"},
            "not_eligible",
            "failed",
            "staging_schema_invalid",
        ),
        (
            "truncated",
            {"truncated": True},
            {"status": "passed"},
            "not_eligible",
            "failed",
            "staging_result_truncated",
        ),
        (
            "shadow_missing",
            {},
            None,
            "need_more_evidence",
            "inconclusive",
            "shadow_missing",
        ),
        (
            "shadow_entity_loss",
            {},
            {"status": "failed", "entity_loss_fraction": 0.5},
            "not_eligible",
            "failed",
            "shadow_entity_loss",
        ),
        (
            "shadow_null_spike",
            {},
            {"status": "failed", "new_null_fraction": 0.5},
            "not_eligible",
            "failed",
            "shadow_null_increase",
        ),
        (
            "shadow_no_match",
            {},
            {"status": "inconclusive", "matched_entities": 0},
            "need_more_evidence",
            "inconclusive",
            "shadow_no_matched_entities",
        ),
        (
            "empty_result",
            {"row_count": 0},
            {"status": "passed"},
            "need_more_evidence",
            "inconclusive",
            "staging_empty_result",
        ),
    ],
)
def test_release_eligibility(case, staging_kwargs, shadow_kwargs, eligibility, status, finding):
    stage = staging(**staging_kwargs)
    shadow = None if shadow_kwargs is None else shadow_result(**shadow_kwargs)
    findings, actual, actual_status, warnings, missing = ReleaseDiagnostics().assess(
        stage, shadow, ReleasePolicy(), synthetic_data=False
    )
    assert actual == eligibility
    assert actual_status == status
    if finding:
        assert finding in {item["type"] for item in findings}
    if shadow_kwargs is not None:
        assert missing == []


def test_release_eligibility_caps_synthetic_evidence():
    _, eligibility, status, warnings, missing = ReleaseDiagnostics().assess(
        staging(), shadow_result("passed"), ReleasePolicy(), synthetic_data=True
    )
    assert (eligibility, status) == ("eligible_for_release_review", "passed_with_warnings")
    assert any("synthetic evidence only" in warning for warning in warnings)
    assert "real Spark not verified" in missing


# ---------------------------------------------------------------- registry E2E


def test_phase6_full_chain(refinement_app, series_id):
    """v1 and v2 registration, release, activation and a reviewed rollback."""
    client, _, rid, proposal, _ = refinement_app
    accept_proposal(client, rid, proposal)
    versions = {}
    for days, expected_version in ((60, "1.0.0"), (90, "2.0.0")):
        review_id = promotion_review(client, refinement_for(client, rid, proposal, days), series_id)
        version = register_version(client, review_id)
        assert version["metric_key"] == "invoice_amount"
        assert version["version"] == expected_version
        assert version["synthetic_data"] is True
        assert version["provenance"]["promotion_review_id"] == review_id
        assert len(version["provenance"]["temporal_report_hash"]) == 64
        versions[days] = version

    assert versions[60]["version_change"]["kind"] == "initial"
    assert versions[90]["version_change"]["kind"] == "major"
    assert versions[90]["version_change"]["previous_version"] == "1.0.0"
    assert {item["field"] for item in versions[90]["version_change"]["changed_fields"]} >= {
        "window.size"
    }

    definition = client.get("/api/v1/metrics/invoice_amount").json()
    assert definition["member_metric_names"] == ["invoice_amount_60d", "invoice_amount_90d"]
    assert definition["active_version_id"] is None
    assert client.get("/api/v1/metrics/invoice_amount/active").json() is None
    listed = client.get("/api/v1/metrics/invoice_amount/versions").json()
    assert [item["version"] for item in listed] == ["1.0.0", "2.0.0"]
    exact = client.get("/api/v1/metrics/invoice_amount/versions/1.0.0").json()
    assert exact["metric_name"] == "invoice_amount_60d"
    assert client.get("/api/v1/metrics/invoice_amount/versions/9.9.9").status_code == 404

    diff = client.get("/api/v1/metrics/invoice_amount/versions/1.0.0/compare/2.0.0").json()
    assert diff["classification"] == "major"
    assert {"field": "window.size", "before": 60, "after": 90} in diff["changed_fields"]

    first_release, first_report = release_and_activate(client, versions[60], None)
    assert first_report["shadow"]["baseline"]["role"] == "lineage_baseline_artifact"
    assert first_report["shadow"]["value_changed"] > 0
    assert first_report["staging"]["status"] == "passed"
    assert (first_report["logical_activation_only"], first_report["production_deployed"]) == (
        True,
        False,
    )
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "1.0.0"

    second_release, second_report = release_and_activate(client, versions[90], "1.0.0")
    assert second_report["shadow"]["baseline"]["role"] == "active_metric_version"
    assert second_report["shadow"]["baseline"]["version"] == "1.0.0"
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "2.0.0"
    assert [
        item["status"] for item in client.get("/api/v1/metrics/invoice_amount/versions").json()
    ] == [
        "retired",
        "active",
    ]

    rollback = client.post(
        "/api/v1/metrics/invoice_amount/rollback",
        json={"target_version": "1.0.0", "reason": "Synthetic regression in 90d candidate"},
    )
    assert rollback.status_code == 201, rollback.text
    rollback_id = rollback.json()["rollback_review_id"]
    assert rollback.json()["from_version"] == "2.0.0"
    approved = client.post(
        f"/api/v1/rollback-reviews/{rollback_id}/decision",
        json={"decision": "approved", "reviewer": "fixture", "comment": "Synthetic rollback"},
    )
    assert approved.status_code == 200, approved.text
    assert client.get("/api/v1/metrics/invoice_amount/active").json()["version"] == "1.0.0"
    # The replaced version is retired, never deleted.
    assert [
        item["version"] for item in client.get("/api/v1/metrics/invoice_amount/versions").json()
    ] == [
        "1.0.0",
        "2.0.0",
    ]
    assert client.get(f"/api/v1/releases/{first_release}").json()["status"] == "active"
    # The replaced release stays readable and records the rollback on its own body.
    replaced = client.get(f"/api/v1/releases/{second_release}").json()
    assert replaced["status"] == "rolled_back"
    assert (replaced["logical_activation_only"], replaced["production_deployed"]) == (True, False)
    events = [
        item["event_type"] for item in client.get("/api/v1/metrics/invoice_amount/events").json()
    ]
    assert events == [
        "version_registered",
        "version_registered",
        "release_created",
        "staging_validated",
        "release_approved",
        "activated",
        "release_created",
        "staging_validated",
        "release_approved",
        "activated",
        "rollback_requested",
        "rollback_approved",
        "rolled_back",
    ]


def test_release_gates_and_state_machine(refinement_app, series_id):
    client, _, rid, proposal, _ = refinement_app
    accept_proposal(client, rid, proposal)
    review_id = promotion_review(client, refinement_for(client, rid, proposal, 60), series_id)
    version = register_version(client, review_id)

    created = client.post(
        "/api/v1/releases",
        json={"metric_version_id": version["metric_version_id"], "target_environment": "staging"},
    )
    assert created.status_code == 201, created.text
    release_id = created.json()["release_id"]
    assert created.json()["status"] == "pending_staging_validation"
    assert created.json()["expected_active_version_id"] is None
    # draft never jumps to active, and review is impossible before validation.
    assert client.post(f"/api/v1/releases/{release_id}/review").status_code == 409
    assert client.post(f"/api/v1/releases/{release_id}/activate", json={}).status_code == 409
    assert client.get(f"/api/v1/releases/{release_id}/validation").status_code == 404

    validated = client.post(f"/api/v1/releases/{release_id}/validate")
    assert validated.status_code == 200, validated.text
    assert client.post(f"/api/v1/releases/{release_id}/validate").status_code == 409
    assert client.get(f"/api/v1/releases/{release_id}/validation").json() == validated.json()

    review = client.post(f"/api/v1/releases/{release_id}/review")
    assert review.status_code == 201, review.text
    review_id_ = review.json()["release_review_id"]
    assert client.post(f"/api/v1/releases/{release_id}/review").status_code == 409
    # Activation is refused while the release review is still pending.
    assert client.post(f"/api/v1/releases/{release_id}/activate", json={}).status_code == 409
    assert (
        client.post(
            f"/api/v1/release-reviews/{review_id_}/decision",
            json={"decision": "need_more_evidence", "reviewer": "fixture", "comment": "Hold"},
        ).status_code
        == 200
    )
    reopened = client.post(f"/api/v1/releases/{release_id}/review")
    assert reopened.status_code == 201, reopened.text
    assert reopened.json()["release_review_id"] == review_id_
    assert reopened.json()["previous_decisions"][0]["decision"] == "need_more_evidence"
    decided = client.post(
        f"/api/v1/release-reviews/{review_id_}/decision",
        json={"decision": "approved", "reviewer": "fixture", "comment": "Synthetic release"},
    )
    assert decided.status_code == 200
    assert (
        client.post(
            f"/api/v1/release-reviews/{review_id_}/decision",
            json={"decision": "rejected", "reviewer": "fixture", "comment": "Duplicate"},
        ).status_code
        == 409
    )

    # Optimistic concurrency: the client believes 1.0.0 is already live, but no
    # version has ever been activated for this family.
    conflict = client.post(
        f"/api/v1/releases/{release_id}/activate",
        json={"expected_active_version": "1.0.0"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "activation_conflict"
    activated = client.post(f"/api/v1/releases/{release_id}/activate", json={})
    assert activated.status_code == 200, activated.text
    # Re-activating the same release is idempotent and does not add an event.
    again = client.post(f"/api/v1/releases/{release_id}/activate", json={})
    assert again.status_code == 200 and again.json()["status"] == "active"
    events = [
        item["event_type"] for item in client.get("/api/v1/metrics/invoice_amount/events").json()
    ]
    assert events.count("activated") == 1
    assert (
        client.post(
            "/api/v1/metrics/invoice_amount/rollback",
            json={"target_version": "1.0.0", "reason": "Only one version exists"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/releases",
            json={
                "metric_version_id": version["metric_version_id"],
                "target_environment": "production",
            },
        ).status_code
        == 409
    )


def test_registry_and_release_tamper(refinement_app, series_id):
    client, app, rid, proposal, _ = refinement_app
    accept_proposal(client, rid, proposal)
    review_id = promotion_review(client, refinement_for(client, rid, proposal, 60), series_id)
    version = register_version(client, review_id)
    same = client.post("/api/v1/metric-versions", json={"promotion_review_id": review_id})
    assert same.status_code == 409
    assert same.json()["error"]["code"] == "metric_version_already_registered"

    from airi.registry.persistence import MetricVersionRow

    with app.state.database.session_factory() as session:
        row = session.get(MetricVersionRow, version["metric_version_id"])
        data = copy.deepcopy(row.version_json)
        data["metric_ir"]["window"]["size"] = 120
        row.version_json = data
        session.commit()
    tampered = client.get("/api/v1/metrics/invoice_amount/versions/1.0.0")
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "metric_registry_integrity_error"


def test_release_evidence_tamper(refinement_app, series_id):
    client, app, rid, proposal, _ = refinement_app
    accept_proposal(client, rid, proposal)
    review_id = promotion_review(client, refinement_for(client, rid, proposal, 60), series_id)
    version = register_version(client, review_id)
    created = client.post(
        "/api/v1/releases",
        json={"metric_version_id": version["metric_version_id"], "target_environment": "staging"},
    )
    release_id = created.json()["release_id"]

    from airi.temporal.persistence import TemporalResultRow

    with app.state.database.session_factory() as session:
        row = session.get(TemporalResultRow, version["source_temporal_validation_run_id"])
        data = copy.deepcopy(row.report_json)
        data["promotion_eligibility"] = "not_eligible"
        row.report_json = data
        session.commit()
    response = client.post(f"/api/v1/releases/{release_id}/validate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "release_evidence_changed"


# --------------------------------------------------------------- request shape


@pytest.mark.parametrize(
    "path,body,extra",
    [
        ("metric-versions", {"promotion_review_id": "r"}, "metric_ir"),
        ("metric-versions", {"promotion_review_id": "r"}, "version"),
        ("metric-versions", {"promotion_review_id": "r"}, "artifact_id"),
        ("metric-versions", {"promotion_review_id": "r"}, "SQL"),
        (
            "releases",
            {"metric_version_id": "v", "target_environment": "staging"},
            "expected_active_version",
        ),
        (
            "releases",
            {"metric_version_id": "v", "target_environment": "staging"},
            "release_policy",
        ),
    ],
)
def test_registry_api_rejects_overrides(client, path, body, extra):
    """Clients submit reviewed identifiers only; content is restored from lineage."""
    response = client.post(f"/api/v1/{path}", json=body | {extra: "forbidden"})
    assert response.status_code == 422
    assert response.headers["x-request-id"]


@pytest.mark.parametrize(
    "model,payload",
    [
        (MetricVersionRequest, {"promotion_review_id": "r", "metric_key": "forged"}),
        (ReleaseRequest, {"metric_version_id": "v", "target_environment": "production", "x": 1}),
        (ReleasePolicy, {"version": "2.0.0"}),
        (ReleasePolicy, {"require_shadow_validation": False}),
        (ReleasePolicy, {"logical_activation_only": False}),
    ],
)
def test_registry_strict_schemas(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)

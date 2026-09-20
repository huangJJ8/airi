import copy
import math
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_refinement import accept_and_create, approve_candidate
from test_refinement import refinement_app as refinement_fixture

from airi.evaluation.calculator import calculate
from airi.evaluation.models import EvaluationPolicy
from airi.experiments.validation import dataset_checksum
from airi.temporal.diagnostics import TemporalDiagnostics
from airi.temporal.fixtures import temporal_fixture
from airi.temporal.models import (
    PromotionPolicy,
    StabilityPolicy,
    TemporalSeriesInput,
    TemporalValidationRequest,
)
from airi.temporal.statistics import direction_consistency, fixed_boundaries, frozen_threshold, psi

refinement_app = refinement_fixture


def decimals(values):
    return [Decimal(v) if v is not None else None for v in values]


@pytest.mark.parametrize("values", [[1, 2, 3], [1, 1, 1], [None, 1, 2], [None, None]])
def test_psi_identical(values):
    data = decimals(values)
    result = psi(data, data, fixed_boundaries(data, 2), 1e-6)
    assert result.value == 0
    assert result.bins[-1].is_null


def test_psi_hand_calculated():
    # Three fixed buckets, including explicit NULL: 50/25/25 -> 25/50/25.
    result = psi(decimals([1, 1, 3, None]), decimals([1, 3, 3, None]), [Decimal(2)], 1e-6)
    e = [(v + 1e-6) / (1 + 3e-6) for v in (0.5, 0.25, 0.25)]
    a = [(v + 1e-6) / (1 + 3e-6) for v in (0.25, 0.5, 0.25)]
    expected = sum((x - y) * math.log(x / y) for x, y in zip(a, e, strict=True))
    assert result.value == pytest.approx(expected)


@pytest.mark.parametrize("actual", [[3, 3, 3], [None, None, None], [1, 3, None]])
def test_psi_shift_zero_bucket(actual):
    result = psi(decimals([1, 1, 1]), decimals(actual), [Decimal(2)], 1e-6)
    assert result.value > 0 and math.isfinite(result.value)
    assert sum(b.actual_pct for b in result.bins) == pytest.approx(1)


def test_empty_and_invalid_psi():
    assert psi([], [Decimal(1)], [], 1e-6).value is None
    assert psi([Decimal(1)], [], [], 1e-6).status == "not_available"
    with pytest.raises(ValueError):
        psi([Decimal(1)], [Decimal(1)], [], 0)
    assert psi([Decimal(1)], [Decimal(2)], [], 1e-6).value == 0


@pytest.mark.parametrize(
    "directions,status",
    [
        (["higher_is_riskier"] * 4, "consistent"),
        (["higher_is_riskier", "lower_is_riskier"], "unstable"),
        (["higher_is_riskier", "undetermined"], "inconclusive"),
    ],
)
def test_direction(directions, status):
    assert direction_consistency("higher_is_riskier", directions)["status"] == status


@pytest.mark.parametrize(
    "rows", [[(Decimal(0), False)] * 10, [(None, True)] * 10, [(Decimal(100), False)] * 10]
)
def test_threshold_empty_hits_or_zero_bad(rows):
    samples = [(Decimal(i), i > 4) for i in range(10)]
    threshold = calculate("test", samples, 10, EvaluationPolicy()).threshold_candidates[-1]
    result = frozen_threshold(rows, threshold)
    assert result.performance.threshold == threshold.threshold
    assert result.performance.lift is None or result.performance.hit_count == 0


def test_strict_requests():
    with pytest.raises(ValidationError):
        TemporalValidationRequest(source_refinement_run_id="r", series_id="s", SQL="SELECT 1")
    with pytest.raises(ValidationError):
        StabilityPolicy(psi_epsilon=0)


def decided_refinement(client, rid, proposal, decision):
    """Run the Phase 4 loop to completion and record an explicit human final decision."""
    refinement = accept_and_create(client, rid, proposal, 60)
    approve_candidate(client, refinement["candidate"])
    refinement_id = refinement["run"]["refinement_run_id"]
    assert (
        client.post(f"/api/v1/refinements/{refinement_id}/evaluate").json()["run"]["status"]
        == "completed"
    )
    response = client.post(
        f"/api/v1/refinements/{refinement_id}/decision",
        json={"decision": decision, "reviewer": "fixture", "comment": "Synthetic research"},
    )
    assert response.status_code == 200, response.text
    return refinement_id


def registered_series(client, app, original, stable, mode=None):
    source, rows, periods = temporal_fixture(stable=stable, mode=mode)
    app.state.query_executor.fixture_rows.extend(source)
    app.state.query_executor.dataset_rows.extend(rows)
    slices = []
    for index, (anchor, dataset) in enumerate(periods):
        snapshot = client.post(
            "/api/v1/datasets",
            json={
                "name": f"temporal_{index}",
                "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
                "snapshot_time": anchor.isoformat(),
                "partition": {"field": "dt", "value": anchor.strftime("%Y%m%d")},
                "row_count": len(dataset),
                "checksum": dataset_checksum(dataset),
            },
        )
        assert snapshot.status_code == 201, snapshot.text
        slices.append(
            {
                "time_slice_id": f"slice-{index}",
                "name": anchor.date().isoformat(),
                "dataset_snapshot_id": snapshot.json()["dataset_snapshot_id"],
                "observation_time": anchor.isoformat(),
                "label_window": {
                    "start": (anchor + timedelta(days=1)).isoformat(),
                    "end": (anchor + timedelta(days=30)).isoformat(),
                },
                "role": "oot" if index == 3 else "historical",
            }
        )
    series = client.post(
        "/api/v1/temporal-series",
        json={
            "name": "temporal fixture",
            "label_definition_id": original["run"]["label_definition_id"],
            "slices": slices,
            "reference_slice_id": "slice-0",
        },
    )
    assert series.status_code == 201, series.text
    return series.json()["series_id"]


def prepare(client, app, rid, proposal, original, stable, mode=None):
    refinement_id = decided_refinement(
        client, rid, proposal, "accept_candidate_for_further_validation"
    )
    series_id = registered_series(client, app, original, stable, mode)
    request = {"source_refinement_run_id": refinement_id, "series_id": series_id}
    result = client.post("/api/v1/temporal-validations", json=request)
    assert result.status_code == 201, result.text
    assert client.post("/api/v1/temporal-validations", json=request).status_code == 409
    return result.json()


@pytest.mark.parametrize("decision", ["keep_baseline", "need_more_evidence", "reject_candidate"])
def test_temporal_requires_further_validation_decision(refinement_app, decision):
    """A non-further-validation decision must never open the temporal validation gate."""
    client, app, rid, proposal, original = refinement_app
    refinement_id = decided_refinement(client, rid, proposal, decision)
    series_id = registered_series(client, app, original, True)
    response = client.post(
        "/api/v1/temporal-validations",
        json={"source_refinement_run_id": refinement_id, "series_id": series_id},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "candidate_not_authorized_for_temporal_validation"


@pytest.mark.parametrize(
    "stable,decision",
    [
        (True, "approved_for_versioning"),
        (True, "rejected"),
        (True, "need_more_evidence"),
        (False, None),
    ],
)
def test_temporal_full_chain(refinement_app, stable, decision):
    client, app, rid, proposal, original = refinement_app
    pending = prepare(client, app, rid, proposal, original, stable)
    run_id = pending["spec"]["temporal_validation_run_id"]
    assert client.post(f"/api/v1/temporal-validations/{run_id}/run").status_code == 409
    for artifact in pending["spec"]["slice_artifacts"]:
        approve_candidate(client, artifact)
    response = client.post(f"/api/v1/temporal-validations/{run_id}/run")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["failure_category"] is None, report
    assert len(report["historical"]) == 3 and report["oot"]
    assert report["synthetic_data"] and report["requires_human_review"]
    assert client.get(f"/api/v1/temporal-validations/{run_id}").json() == report
    assert client.post(f"/api/v1/temporal-validations/{run_id}/run").status_code == 409
    review = client.post("/api/v1/promotion-reviews", json={"temporal_validation_run_id": run_id})
    if stable:
        assert report["promotion_eligibility"] == "eligible_for_review", report["diagnostics"]
        assert review.status_code == 201, review.text
        review_id = review.json()["promotion_review_id"]
        decision = client.post(
            f"/api/v1/promotion-reviews/{review_id}/decision",
            json={
                "decision": decision,
                "reviewer": "fixture",
                "comment": "Synthetic demonstration only",
            },
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["production_deployed"] is False
        assert (
            client.post(
                f"/api/v1/promotion-reviews/{review_id}/decision",
                json={"decision": "rejected", "reviewer": "fixture", "comment": "Duplicate"},
            ).status_code
            == 409
        )
    else:
        assert report["promotion_eligibility"] != "eligible_for_review"
        assert review.status_code == 409
        assert any(d["type"] == "risk_direction_flip" for d in report["diagnostics"])


def test_temporal_oot_degradation_is_need_more_evidence(refinement_app):
    """Historically improved but OOT-degraded evidence must stop at need_more_evidence."""
    client, app, rid, proposal, original = refinement_app
    pending = prepare(client, app, rid, proposal, original, True, mode="oot_degradation")
    run_id = pending["spec"]["temporal_validation_run_id"]
    for artifact in pending["spec"]["slice_artifacts"]:
        approve_candidate(client, artifact)
    response = client.post(f"/api/v1/temporal-validations/{run_id}/run")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["failure_category"] is None, report
    kinds = {d["type"] for d in report["diagnostics"]}
    assert "oot_degradation" in kinds, report["diagnostics"]
    assert "risk_direction_flip" not in kinds, report["diagnostics"]
    assert report["improvement_pattern"] == {"improved": 3, "worse": 1}
    assert report["promotion_eligibility"] == "need_more_evidence", report["diagnostics"]
    assert report["status"] == "failed"
    assert (
        client.post(
            "/api/v1/promotion-reviews", json={"temporal_validation_run_id": run_id}
        ).status_code
        == 409
    )


def diagnostic_matrix():
    value = {
        "coverage": 0.95,
        "ks": 0.5,
        "iv": 0.8,
        "labeled_sample": 200,
        "direction": "higher_is_riskier",
        "psi": {"value": 0.0},
        "frozen_thresholds": [{"deltas": {"precision": 0.0, "recall": 0.0, "lift": 0.0}}],
    }
    return [
        {
            "time_slice_id": str(i),
            "baseline": copy.deepcopy(value),
            "candidate": copy.deepcopy(value),
            "comparison": {"coverage_delta": 0.0, "ks_delta": 0.1, "outcome": "improved"},
        }
        for i in range(4)
    ]


@pytest.mark.parametrize(
    "case,eligibility,finding",
    [
        ("stable", "eligible_for_review", None),
        ("oot_worse", "need_more_evidence", "oot_degradation"),
        ("single_class", "need_more_evidence", "insufficient_temporal_evidence"),
        ("small_sample", "need_more_evidence", "insufficient_temporal_evidence"),
        ("missing_oot", "need_more_evidence", "insufficient_temporal_evidence"),
        ("flip", "not_eligible", "risk_direction_flip"),
        ("psi", "need_more_evidence", "population_shift"),
        ("coverage", "need_more_evidence", "coverage_drift"),
        ("ks", "need_more_evidence", "ks_degradation"),
        ("iv", "eligible_for_review", "iv_instability"),
        ("precision", "need_more_evidence", "threshold_degradation"),
        ("recall", "need_more_evidence", "threshold_degradation"),
        ("lift", "need_more_evidence", "threshold_degradation"),
        ("unavailable_threshold", "need_more_evidence", "insufficient_temporal_evidence"),
    ],
)
def test_diagnostic_policy(case, eligibility, finding):
    matrix = diagnostic_matrix()
    value = matrix[-1]["candidate"]
    if case == "oot_worse":
        matrix[-1]["comparison"].update(outcome="worse", ks_delta=-0.2)
    elif case == "single_class":
        value["ks"] = None
    elif case == "small_sample":
        value["labeled_sample"] = 10
    elif case == "missing_oot":
        matrix.pop()
    elif case == "flip":
        value["direction"] = "lower_is_riskier"
    elif case == "psi":
        value["psi"]["value"] = 0.4
    elif case == "coverage":
        value["coverage"] = 0.5
    elif case == "ks":
        value["ks"] = 0.1
    elif case == "iv":
        value["iv"] = 4.0
    elif case in ("precision", "recall", "lift"):
        value["frozen_thresholds"][0]["deltas"][case] = -1.0
    elif case == "unavailable_threshold":
        value["frozen_thresholds"] = []
    _, findings, actual, status = TemporalDiagnostics().assess(
        matrix, "0", "3", StabilityPolicy(), PromotionPolicy(), EvaluationPolicy()
    )
    assert actual == eligibility
    if finding:
        assert finding in {f["type"] for f in findings}
    if case in ("single_class", "small_sample", "missing_oot"):
        assert status == "inconclusive"


@pytest.mark.parametrize(
    "override",
    [
        "SQL",
        "metric_ir",
        "threshold",
        "evaluation_policy",
        "baseline_artifact_id",
        "candidate_artifact_id",
        "dataset_snapshot_id",
    ],
)
def test_temporal_api_overrides(client, override):
    response = client.post(
        "/api/v1/temporal-validations",
        json={"source_refinement_run_id": "r", "series_id": "s", override: "forbidden"},
    )
    assert response.status_code == 422
    assert response.headers["x-request-id"]


def series_payload():
    return {
        "name": "test",
        "label_definition_id": "label",
        "reference_slice_id": "s0",
        "slices": [
            {
                "time_slice_id": f"s{i}",
                "dataset_snapshot_id": f"d{i}",
                "name": f"month{i}",
                "observation_time": f"2026-0{i + 1}-01T00:00:00+08:00",
                "role": "oot" if i == 3 else "historical",
                "label_window": {
                    "start": f"2026-0{i + 1}-02T00:00:00+08:00",
                    "end": f"2026-0{i + 1}-20T00:00:00+08:00",
                },
            }
            for i in range(4)
        ],
    }


@pytest.mark.parametrize(
    "case", ["leak", "duplicate", "few", "missing_oot", "reference_oot", "reference_later"]
)
def test_invalid_series(case):
    data = series_payload()
    if case == "leak":
        data["slices"][0]["label_window"]["start"] = data["slices"][0]["observation_time"]
    elif case == "duplicate":
        data["slices"][1]["dataset_snapshot_id"] = "d0"
    elif case == "few":
        data["slices"].pop()
    elif case == "missing_oot":
        data["slices"][-1]["role"] = "historical"
    elif case == "reference_ot":
        data["reference_slice_id"] = "s3"
    else:
        data["reference_slice_id"] = "s2"
    with pytest.raises(ValidationError):
        TemporalSeriesInput.model_validate(data)


@pytest.mark.parametrize("case", ["environment", "snapshot", "report", "test_failure"])
def test_temporal_tamper_and_failure(refinement_app, case):
    client, app, rid, proposal, original = refinement_app
    pending = prepare(client, app, rid, proposal, original, True)
    run_id = pending["spec"]["temporal_validation_run_id"]
    for artifact in pending["spec"]["slice_artifacts"]:
        approve_candidate(client, artifact)
    if case == "environment":
        app.state.settings.spark_host = "changed"
    elif case == "test_failure":
        app.state.query_executor.failure = "timeout"
    else:
        from airi.temporal.persistence import TemporalResultRow, TemporalSeriesRow

        with app.state.database.session_factory() as session:
            if case == "report":
                row = session.get(TemporalResultRow, run_id)
                data = copy.deepcopy(row.report_json)
                data["promotion_eligibility"] = "eligible_for_review"
                row.report_json = data
            else:
                row = session.get(TemporalSeriesRow, pending["spec"]["series_id"])
                data = copy.deepcopy(row.series_json)
                data["name"] = "tampered"
                row.series_json = data
            session.commit()
    result = client.post(f"/api/v1/temporal-validations/{run_id}/run")
    if case == "test_failure":
        assert result.status_code == 200
        assert result.json()["failure_category"] == "temporal_metric_test_failed"
    else:
        assert result.status_code == 409

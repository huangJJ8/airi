import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_experiments import setup_experiment
from test_phase2 import NAMES, app_for
from test_reflection import evidence as evidence_fixture

from airi.approvals.artifacts import canonical_hash
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.metric_ir.models import MetricIR
from airi.refinement.comparison import BaselineCandidateComparison
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture
from airi.refinement.models import RefinementComparisonPolicy
from airi.refinement.transform import CandidateIRTransformer, RefinementError

evidence = evidence_fixture


def baseline_ir():
    return MetricIR.model_validate_json(
        Path("examples/invoice_amount_30d_ir.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("days", [60, 90])
def test_transform(days):
    base = baseline_ir()
    before = canonical_hash(base)
    candidate, diff = CandidateIRTransformer().transform_window(base, days)
    assert candidate.name == f"invoice_amount_{days}d"
    assert candidate.window.size == days
    assert canonical_hash(base) == before
    assert canonical_hash(
        CandidateIRTransformer().transform_window(base, days)[0]
    ) == canonical_hash(candidate)
    assert set(c.field for c in diff.changed_fields) == {
        "name",
        "display_name",
        "description",
        "window.size",
    }


@pytest.mark.parametrize("days", [0, -1, 3650, 31, True])
def test_bad_windows(days):
    with pytest.raises(RefinementError):
        CandidateIRTransformer().transform_window(baseline_ir(), days)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", {"database": "evil", "table": "other"}),
        ("entity_key", "buyer_tax_no"),
        ("aggregation", {"function": "avg", "field": "invoice_amt"}),
        ("filters", [{"field": "invoice_amt", "operator": "gt", "value": 0}]),
    ],
)
def test_semantic_tampering(field, value):
    base = baseline_ir()
    candidate, _ = CandidateIRTransformer().transform_window(base, 60)
    data = candidate.model_dump()
    data[field] = value
    with pytest.raises((ValidationError, RefinementError)):
        CandidateIRTransformer().semantic_diff(base, MetricIR.model_validate(data))


@pytest.mark.parametrize(
    "c,k,outcome",
    [
        (0.8, 0.4, "improved"),
        (0.9, 0.3, "improved"),
        (0.7, 0.4, "mixed"),
        (0.9, 0.2, "mixed"),
        (0.7, 0.2, "worse"),
        (0.8, 0.3, "inconclusive"),
        (0.8, None, "inconclusive"),
    ],
)
def test_hand_comparison(evidence, c, k, outcome):
    base = evidence.model_copy(
        update={
            "evaluation": evidence.evaluation.model_copy(
                update={
                    "coverage": 0.8,
                    "ks": evidence.evaluation.ks.model_copy(update={"value": 0.3}),
                }
            )
        }
    )
    candidate = base.model_copy(
        update={
            "evaluation": base.evaluation.model_copy(
                update={"coverage": c, "ks": base.evaluation.ks.model_copy(update={"value": k})}
            )
        }
    )
    result = BaselineCandidateComparison().compare(base, candidate, RefinementComparisonPolicy())
    assert result.outcome == outcome
    assert result.coverage_delta == pytest.approx(c - 0.8)


@pytest.fixture
def refinement_app(settings):
    settings.execution_mode = "mock"
    source, rows = refinement_fixture()
    executor = MockQueryExecutor(source, dataset_rows=rows)
    app = app_for(settings, NAMES[0], executor)
    app.state.reflection_llm = MockWindowReflectionLLM()
    app.state.reflection_model = MockWindowReflectionLLM.model_identifier
    app.state.reflection_output_kind = "mock"
    with TestClient(app) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        exp = client.post(f"/api/v1/experiments/{spec['experiment_spec_id']}/run").json()
        reflection = client.post(
            "/api/v1/reflections", json={"experiment_run_ids": [exp["run"]["experiment_run_id"]]}
        ).json()
        assert reflection["llm_reflection_status"] == "completed", reflection
        rid = reflection["run"]["reflection_run_id"]
        proposal = client.get(f"/api/v1/reflections/{rid}/proposals").json()[0]
        yield client, app, rid, proposal, exp


def accept_and_create(client, rid, proposal, days):
    response = client.post(
        f"/api/v1/reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
        json={
            "decision": "accepted_for_investigation",
            "reviewer": "fixture",
            "comment": "Research only",
        },
    )
    assert response.status_code == 200, response.text
    result = client.post(
        "/api/v1/refinements",
        json={
            "reflection_run_id": rid,
            "proposal_id": proposal["proposal_id"],
            "parameter_selection": {"window_days": days},
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def approve_candidate(client, candidate):
    approved = client.post(
        "/api/v1/approvals",
        json={
            "artifact_id": candidate["artifact_id"],
            "metric_ir_hash": candidate["metric_ir_hash"],
            "artifact_hash": candidate["content_hash"],
        },
    )
    assert approved.status_code == 201, approved.text
    aid = approved.json()["approval_id"]
    result = client.post(
        f"/api/v1/approvals/{aid}/approve",
        json={"reviewer": "fixture", "comment": "Candidate SQL test only"},
    )
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("days,start", [(60, "2026-07-11"), (90, "2026-06-11")])
def test_refinement_e2e(refinement_app, days, start):
    client, app, rid, proposal, exp = refinement_app
    report = accept_and_create(client, rid, proposal, days)
    run_id = report["run"]["refinement_run_id"]
    assert client.post(f"/api/v1/refinements/{run_id}/evaluate").status_code == 409
    artifact = client.get(
        f"/api/v1/development/artifacts/{report['candidate']['artifact_id']}"
    ).json()
    assert start in artifact["code"] and "2026-09-09" in artifact["code"]
    approve_candidate(client, report["candidate"])
    result = client.post(f"/api/v1/refinements/{run_id}/evaluate")
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["run"]["status"] == "completed", final
    assert final["comparison"]["baseline"] == exp["evaluation"]
    assert final["synthetic_data"] and final["requires_human_review"]
    assert final["run"]["candidate_test_run_id"] and final["run"]["candidate_experiment_run_id"]
    assert client.get(f"/api/v1/refinements/{run_id}").json() == final
    assert client.post(f"/api/v1/refinements/{run_id}/evaluate").status_code == 409
    assert (
        client.post(
            f"/api/v1/refinements/{run_id}/decision",
            json={
                "decision": "keep_baseline",
                "reviewer": "fixture",
                "comment": "No production change",
            },
        ).status_code
        == 200
    )


@pytest.mark.parametrize("decision", ["pending", "rejected", "need_more_evidence"])
def test_unaccepted(refinement_app, decision):
    client, _, rid, p, _ = refinement_app
    response = client.post(
        f"/api/v1/reflections/{rid}/proposals/{p['proposal_id']}/decision",
        json={"decision": decision, "reviewer": "fixture", "comment": "Not accepted"},
    )
    assert response.status_code == 200
    response = client.post(
        "/api/v1/refinements",
        json={
            "reflection_run_id": rid,
            "proposal_id": p["proposal_id"],
            "parameter_selection": {"window_days": 60},
        },
    )
    assert response.status_code == 409 and "refinement_not_authorized" in response.text


def test_candidate_test_failure(refinement_app):
    client, app, rid, p, _ = refinement_app
    report = accept_and_create(client, rid, p, 60)
    approve_candidate(client, report["candidate"])
    app.state.query_executor.failure = "timeout"
    result = client.post(
        f"/api/v1/refinements/{report['run']['refinement_run_id']}/evaluate"
    ).json()
    assert result["run"]["status"] == "failed"
    assert result["run"]["failure_category"] == "candidate_test_failed"
    assert result["run"]["candidate_experiment_run_id"] is None


def test_no_client_override(client):
    assert client.post("/api/v1/refinements", json={"SQL": "SELECT 1"}).status_code == 422
    assert (
        client.post(
            "/api/v1/refinements/none/evaluate", json={"baseline_id": "override"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("initial", ["pending", "need_more_evidence"])
def test_decision_transition(refinement_app, initial):
    client, _, rid, proposal, _ = refinement_app
    url = f"/api/v1/reflections/{rid}/proposals/{proposal['proposal_id']}/decision"
    first = client.post(
        url, json={"decision": initial, "reviewer": "first", "comment": "Await evidence"}
    )
    assert first.status_code == 200
    report = accept_and_create(client, rid, proposal, 60)
    assert report["plan"]["decision"]["previous_decisions"][0]["decision"] == initial
    assert (
        client.post(
            url, json={"decision": "rejected", "reviewer": "later", "comment": "Cannot overwrite"}
        ).status_code
        == 409
    )


def test_manual_and_unselected_parameter(refinement_app):
    from airi.reflection.llm import MockReflectionLLM

    client, app, rid, proposal, exp = refinement_app
    accept_and_create(client, rid, proposal, 60)
    invalid = client.post(
        "/api/v1/refinements",
        json={
            "reflection_run_id": rid,
            "proposal_id": proposal["proposal_id"],
            "parameter_selection": {"window_days": 31},
        },
    )
    assert invalid.status_code == 409 and "proposal_parameter_invalid" in invalid.text
    app.state.reflection_llm = MockReflectionLLM()
    reflection = client.post(
        "/api/v1/reflections", json={"experiment_run_ids": [exp["run"]["experiment_run_id"]]}
    ).json()
    other_id = reflection["run"]["reflection_run_id"]
    manual = client.get(f"/api/v1/reflections/{other_id}/proposals").json()[0]
    assert manual["automation_capability"] == "manual_only"
    client.post(
        f"/api/v1/reflections/{other_id}/proposals/{manual['proposal_id']}/decision",
        json={
            "decision": "accepted_for_investigation",
            "reviewer": "fixture",
            "comment": "Manual study",
        },
    )
    blocked = client.post(
        "/api/v1/refinements",
        json={
            "reflection_run_id": other_id,
            "proposal_id": manual["proposal_id"],
            "parameter_selection": {"window_days": 60},
        },
    )
    assert blocked.status_code == 409 and "proposal_manual_only" in blocked.text


def test_experiment_failure(refinement_app):
    from airi.execution.models import QueryOutput

    client, app, rid, p, _ = refinement_app
    report = accept_and_create(client, rid, p, 60)
    approve_candidate(client, report["candidate"])
    app.state.query_executor.load_dataset = lambda snapshot, plan: QueryOutput(columns=[], rows=[])
    result = client.post(
        f"/api/v1/refinements/{report['run']['refinement_run_id']}/evaluate"
    ).json()
    assert result["run"]["status"] == "failed"
    assert result["run"]["failure_category"] == "refinement_evaluation_failed"
    assert result["run"]["candidate_test_run_id"] and result["run"]["candidate_experiment_run_id"]
    assert result["comparison"] is None and result["run"]["outcome"] == "inconclusive"


@pytest.mark.parametrize(
    "field", ["dataset_snapshot_id", "label_definition_id", "evaluation_policy"]
)
def test_comparison_fairness(evidence, field):
    value = (
        evidence.evaluation_policy.model_copy(update={"bins": 5})
        if field == "evaluation_policy"
        else "different"
    )
    with pytest.raises(RefinementError, match="comparison_invalid"):
        BaselineCandidateComparison().compare(
            evidence, evidence.model_copy(update={field: value}), RefinementComparisonPolicy()
        )


def test_input_hash_and_baseline_immutable(refinement_app):
    from airi.approvals.persistence import DevelopmentArtifactRow
    from airi.reflection.persistence import ReflectionReportRow

    client, app, rid, p, exp = refinement_app
    report = accept_and_create(client, rid, p, 60)
    request = report["plan"]["request"]
    duplicate = client.post("/api/v1/refinements", json=request).json()
    assert duplicate["plan"]["refinement_input_hash"] == report["plan"]["refinement_input_hash"]
    assert duplicate["definition"]["metric_ir_hash"] == report["definition"]["metric_ir_hash"]
    assert duplicate["candidate"]["content_hash"] == report["candidate"]["content_hash"]
    with app.state.database.session_factory() as session:
        baseline = session.get(DevelopmentArtifactRow, exp["run"]["artifact_id"])
        assert baseline.metric_ir_hash == canonical_hash(baseline_ir())
        stored = session.get(ReflectionReportRow, rid)
        data = json.loads(json.dumps(stored.report_json))
        data["summary"] += " changed"
        stored.report_json = data
        session.commit()
    assert (
        client.post(
            f"/api/v1/refinements/{report['run']['refinement_run_id']}/evaluate"
        ).status_code
        == 409
    )


def test_candidate_test_warnings_are_retained(refinement_app):
    from airi.testing.persistence import MetricTestRunRow

    client, app, rid, p, _ = refinement_app
    report = accept_and_create(client, rid, p, 60)
    approve_candidate(client, report["candidate"])
    app.state.query_executor.fixture_rows.append(
        {"seller_tax_no": None, "invoice_date": "2026-07-15", "invoice_amt": "1", "dt": "20260715"}
    )
    result = client.post(
        f"/api/v1/refinements/{report['run']['refinement_run_id']}/evaluate"
    ).json()
    assert result["run"]["status"] == "completed", result
    with app.state.database.session_factory() as session:
        tested = session.get(MetricTestRunRow, result["run"]["candidate_test_run_id"])
        assert tested.status == "passed_with_warnings"
    assert any("passed_with_warnings" in warning for warning in result["warnings"])

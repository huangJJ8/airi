import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_experiments import setup_experiment
from test_phase2 import NAMES, app_for

from airi.experiments.fixtures import experiment_fixture
from airi.infrastructure.llm import MockLLMClient
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.reflection.diagnostics import ReflectionDiagnostics, evidence_references
from airi.reflection.evidence import (
    ComparisonCompatibilityGuard,
    EvidenceExtractor,
    ReflectionError,
)
from airi.reflection.llm import MockReflectionLLM
from airi.reflection.models import (
    ReflectionContext,
    ReflectionLLMOutput,
    ReflectionPolicy,
)
from airi.reflection.validation import RefinementProposalValidator


@pytest.fixture
def evidence(settings):
    # Persist an actual Phase 3 chain, not an invented evaluation report.
    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    app = app_for(settings, NAMES[0], MockQueryExecutor(source, dataset_rows=rows))
    with TestClient(app) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        result = client.post(f"/api/v1/experiments/{spec['experiment_spec_id']}/run").json()
        with app.state.database.session_factory() as session:
            yield EvidenceExtractor(session, app.state.skills).extract(
                result["run"]["experiment_run_id"]
            )


def context_for(evidence):
    policy = ReflectionPolicy()
    return ReflectionContext(
        evidence=[evidence],
        diagnostics=ReflectionDiagnostics().analyze([evidence], policy),
        comparison=None,
        policy=policy,
        references=evidence_references([evidence]),
        missing_evidence=[],
    )


def output_for(context):
    return json.loads(
        MockReflectionLLM().complete_structured(
            system_prompt="", user_prompt=context.model_dump_json(), schema={}
        )
    )


@pytest.mark.parametrize(
    "change,expected",
    [
        ("high", "strong_separation"),
        ("low", "high_null_rate"),
        ("weak", "weak_separation"),
        ("direction", "risk_direction_uncertain"),
        ("bins", "few_effective_bins"),
        ("small", "small_sample"),
        ("single", "single_class"),
        ("iv", "high_iv_sensitivity"),
        ("tradeoff", "threshold_tradeoff"),
    ],
)
def test_diagnostics(evidence, change, expected):
    changes = {
        "high": {"coverage": 1.0},
        "low": {"coverage": 0.2},
        "weak": {"ks": evidence.evaluation.ks.model_copy(update={"value": 0.07})},
        "direction": {
            "ks": evidence.evaluation.ks.model_copy(update={"direction": "undetermined"})
        },
        "bins": {"actual_bins": 1},
        "small": {"labeled_sample": 20},
        "single": {"bad_count": 0},
        "iv": {},
        "tradeoff": {},
    }
    changed = evidence.model_copy(
        update={"evaluation": evidence.evaluation.model_copy(update=changes[change])}
    )
    findings = ReflectionDiagnostics().analyze([changed], ReflectionPolicy())
    assert expected in {f.finding_type for f in findings}
    if change == "high":
        assert "coverage_gap" not in {f.finding_type for f in findings}
    if change == "low":
        assert any(f.pattern == "strong_separation_with_coverage_gap" for f in findings)


@pytest.mark.parametrize(
    "field",
    [
        "dataset_snapshot_id",
        "label_definition_id",
        "observation_time",
        "label_window",
        "evaluation_policy",
        "synthetic_data",
    ],
)
def test_incomparable(evidence, field):
    changes = {
        "dataset_snapshot_id": "different",
        "label_definition_id": "different",
        "observation_time": evidence.observation_time + timedelta(days=1),
        "label_window": evidence.label_window.model_copy(
            update={"end": evidence.label_window.end + timedelta(days=1)}
        ),
        "evaluation_policy": evidence.evaluation_policy.model_copy(update={"bins": 5}),
        "synthetic_data": False,
    }
    with pytest.raises(ReflectionError, match="Experiments must share"):
        ComparisonCompatibilityGuard().validate(
            [evidence, evidence.model_copy(update={field: changes[field]})]
        )


def test_comparable_and_input(evidence):
    comparison = ComparisonCompatibilityGuard().validate([evidence, evidence])
    assert comparison.comparable and not hasattr(comparison, "winner")
    context = context_for(evidence)
    assert not {"rows", "entity_id", "code", "sql"} & set(context.model_dump())
    output = RefinementProposalValidator().validate(
        ReflectionLLMOutput.model_validate(output_for(context)), context
    )
    assert output.proposals[0].priority == "high"
    assert output.hypotheses[0].status == "unverified"


@pytest.mark.parametrize(
    "mutation",
    [
        "type",
        "evidence",
        "threshold",
        "sql",
        "deploy",
        "operator",
        "hallucination",
        "window",
        "action",
        "target",
        "stable",
        "limit",
    ],
)
def test_proposal_rejections(evidence, mutation):
    context = context_for(evidence)
    raw = output_for(context)
    p = raw["proposals"][0]
    if mutation == "type":
        p["proposal_type"] = "arbitrary_code_change"
    elif mutation == "evidence":
        p["evidence_refs"] = ["finding:invented"]
    elif mutation == "threshold":
        p.update(
            proposal_type="threshold_review",
            action="review_existing_candidates",
            parameters={"candidate_refs": ["invented:87.361"]},
        )
    elif mutation == "sql":
        p["reason"] = "SELECT invoice_amt FROM invoice"
    elif mutation == "deploy":
        p["reason"] = "Deploy to production"
    elif mutation == "operator":
        p["parameters"] = {"operator": ">"}
    elif mutation == "hallucination":
        raw["summary"] = "KS = 0.99"
    elif mutation == "window":
        p.update(
            proposal_type="window_review",
            action="evaluate_alternative_window",
            parameters={"candidate_windows": [-1]},
        )
    elif mutation == "action":
        p["action"] = "execute_sql"
    elif mutation == "target":
        p["target"] = "invented_metric"
    elif mutation == "stable":
        raw["summary"] = "指标稳定"
    elif mutation == "limit":
        raw["proposals"] *= 6
    with pytest.raises((ValidationError, ReflectionError)):
        RefinementProposalValidator().validate(ReflectionLLMOutput.model_validate(raw), context)


def test_threshold_and_window_proposals(evidence):
    context = context_for(evidence)
    raw = output_for(context)
    p = raw["proposals"][0]
    p.update(
        proposal_type="threshold_review",
        action="review_existing_candidates",
        parameters={
            "candidate_refs": [next(r.ref for r in context.references if ":candidate:" in r.ref)]
        },
    )
    assert RefinementProposalValidator().validate(ReflectionLLMOutput.model_validate(raw), context)
    p.update(
        proposal_type="window_review",
        action="evaluate_alternative_window",
        parameters={"candidate_windows": [60, 90]},
    )
    assert RefinementProposalValidator().validate(ReflectionLLMOutput.model_validate(raw), context)


@pytest.mark.parametrize("name", NAMES)
def test_reflection_e2e(settings, name):
    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    app = app_for(settings, name, MockQueryExecutor(source, dataset_rows=rows))
    app.state.reflection_llm = MockReflectionLLM()
    app.state.reflection_model = MockReflectionLLM.model_identifier
    app.state.reflection_output_kind = "mock"
    with TestClient(app) as client:
        payload = setup_experiment(client, name, rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        experiment = client.post(f"/api/v1/experiments/{spec['experiment_spec_id']}/run").json()
        run_id = experiment["run"]["experiment_run_id"]
        request = {"experiment_run_ids": [run_id]}
        response = client.post("/api/v1/reflections", json=request)
        assert response.status_code == 201, response.text
        assert response.headers["X-Request-ID"]
        report = response.json()
        assert report["llm_reflection_status"] == "completed", report
        assert report["evidence"][0]["evaluation"] == experiment["evaluation"]
        assert report["evidence"][0]["synthetic_data"]
        assert report["refinement_proposals"] and report["requires_human_review"]
        assert (
            client.get(f"/api/v1/reflections/{report['run']['reflection_run_id']}").json() == report
        )
        again = client.post("/api/v1/reflections", json=request).json()
        assert again["run"]["input_evidence_hash"] == report["run"]["input_evidence_hash"]
        assert again["run"]["reflection_run_id"] != report["run"]["reflection_run_id"]
        app.state.reflection_llm = MockLLMClient('{"summary":"KS=0.99"}')
        rejected = client.post("/api/v1/reflections", json=request).json()
        assert rejected["llm_reflection_status"] == "rejected"
        assert rejected["evidence"][0]["evaluation"] == experiment["evaluation"]
        assert not rejected["refinement_proposals"]


def test_api_input_guards(client):
    assert client.get("/api/v1/reflections/missing").status_code == 404
    assert (
        client.post("/api/v1/reflections", json={"experiment_run_ids": ["missing"]}).status_code
        == 404
    )
    for raw in (
        {"experiment_run_ids": ["x"], "ks": 0.99},
        {"experiment_run_ids": ["x"], "mode": "comparison"},
        {"experiment_run_ids": ["x", "x"], "mode": "comparison"},
        {"experiment_run_ids": ["x"], "sql": "SELECT 1"},
    ):
        assert client.post("/api/v1/reflections", json=raw).status_code == 422


@pytest.mark.parametrize(
    "failure", ["unavailable", "invalid_json", "failed_run", "hash", "missing_report"]
)
def test_failures_and_provenance(settings, failure):
    from airi.experiments.persistence import ExperimentEvaluationRow, ExperimentRunRow

    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    app = app_for(settings, NAMES[0], MockQueryExecutor(source, dataset_rows=rows))
    with TestClient(app) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        experiment = client.post(f"/api/v1/experiments/{spec['experiment_spec_id']}/run").json()
        run_id = experiment["run"]["experiment_run_id"]
        if failure in ("failed_run", "hash", "missing_report"):
            with app.state.database.session_factory() as session:
                if failure == "failed_run":
                    session.get(ExperimentRunRow, run_id).status = "failed"
                else:
                    row = session.get(ExperimentEvaluationRow, run_id)
                    if failure == "missing_report":
                        session.delete(row)
                    else:
                        value = json.loads(json.dumps(row.report_json))
                        value["reproducibility"]["metric_ir_hash"] = "0" * 64
                        row.report_json = value
                session.commit()
        elif failure == "invalid_json":
            app.state.reflection_llm = MockLLMClient("not json")
        else:
            # Default unconfigured provider must return diagnostics, never a silent Mock.
            assert app.state.settings.llm_model == "configure-your-model"
        response = client.post("/api/v1/reflections", json={"experiment_run_ids": [run_id]})
        if failure in ("failed_run", "hash", "missing_report"):
            assert response.status_code == (404 if failure == "missing_report" else 409)
        else:
            assert response.status_code == 201, response.text
            report = response.json()
            assert report["run"]["status"] == "diagnostics_only"
            assert report["llm_reflection_status"] == (
                "rejected" if failure == "invalid_json" else "unavailable"
            )
            assert (
                report["diagnostics"]
                and not report["hypotheses"]
                and not report["refinement_proposals"]
            )
            assert (
                client.get(f"/api/v1/reflections/{report['run']['reflection_run_id']}").json()
                == report
            )


def test_comparison_e2e(settings):
    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    app = app_for(settings, NAMES[0], MockQueryExecutor(source, dataset_rows=rows))
    app.state.reflection_llm = MockReflectionLLM()
    with TestClient(app) as client:
        shared = None
        ids = []
        for name in NAMES:
            app.state.development_workflow.parser.llm = MockLLMClient(
                json.dumps(
                    {
                        "metric_ir": json.loads(
                            Path(f"examples/{name}_ir.json").read_text(encoding="utf-8")
                        ),
                        "unsupported_reason": None,
                    }
                )
            )
            payload = setup_experiment(client, name, rows)
            if shared is None:
                shared = {k: payload[k] for k in ("dataset_snapshot_id", "label_definition_id")}
            else:
                payload.update(shared)
            spec = client.post("/api/v1/experiments", json=payload)
            assert spec.status_code == 201, spec.text
            experiment = client.post(
                f"/api/v1/experiments/{spec.json()['experiment_spec_id']}/run"
            ).json()
            ids.append(experiment["run"]["experiment_run_id"])
        response = client.post(
            "/api/v1/reflections", json={"experiment_run_ids": ids, "mode": "comparison"}
        )
        assert response.status_code == 201, response.text
        report = response.json()
        assert report["comparison"]["comparable"]
        assert len(report["refinement_proposals"]) == 3
        assert (
            sum(f["finding_type"] == "cross_metric_difference" for f in report["diagnostics"]) == 3
        )
        reversed_report = client.post(
            "/api/v1/reflections", json={"experiment_run_ids": ids[::-1], "mode": "comparison"}
        ).json()
        assert reversed_report["run"]["input_evidence_hash"] == report["run"]["input_evidence_hash"]
        separate = setup_experiment(client, NAMES[-1], rows)
        other = client.post("/api/v1/experiments", json=separate).json()
        other_run = client.post(f"/api/v1/experiments/{other['experiment_spec_id']}/run").json()
        rejected = client.post(
            "/api/v1/reflections",
            json={
                "experiment_run_ids": [ids[0], other_run["run"]["experiment_run_id"]],
                "mode": "comparison",
            },
        )
        assert rejected.status_code == 409
        assert "experiments_not_comparable" in rejected.text


def test_no_raw_data_in_prompt(evidence):
    context = context_for(evidence)

    def scan(value):
        if isinstance(value, dict):
            assert not {"entity_id", "rows", "code", "sql", "bad_flag"} & set(value)
            for nested in value.values():
                scan(nested)
        elif isinstance(value, list):
            for nested in value:
                scan(nested)

    scan(context.model_dump(mode="json"))


def test_policy_and_hypothesis_validation(evidence):
    from airi.reflection.models import DiagnosticBands

    with pytest.raises(ValidationError):
        DiagnosticBands(ks_review_below=0.5, ks_strong_at_least=0.2)
    context = context_for(evidence)
    output = output_for(context)
    output["hypotheses"][0]["statement"] = "Missingness is caused by corrupted invoice dates."
    with pytest.raises(ReflectionError):
        RefinementProposalValidator().validate(ReflectionLLMOutput.model_validate(output), context)

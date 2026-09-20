import json
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from airi.approvals.artifacts import artifact_content_hash, canonical_hash
from airi.approvals.persistence import ApprovalRecordRow, DevelopmentArtifactRow
from airi.approvals.service import ApprovalService
from airi.core.exceptions import (
    ApprovalArtifactChangedError,
    ApprovalStateError,
    PlanningError,
    SQLValidationError,
    ToolOutputError,
)
from airi.infrastructure.llm import MockLLMClient
from airi.main import create_app
from airi.workflows.development.derived_validation import DerivedSQLValidator
from airi.workflows.development.schemas import DevelopmentRequest


@pytest.fixture
def atomic_payload():
    return json.loads(Path("examples/invoice_metric_ir.json").read_text(encoding="utf-8"))


@pytest.fixture
def growth_payload(atomic_payload):
    return {
        "schema_version": "1.0.0",
        "metric_type": "derived",
        "name": "invoice_amount_growth_30d",
        "display_name": "近30天企业开票金额增长率",
        "description": "本30日开票金额相对前30日的增长率",
        "dependencies": [
            {"role": "current", "metric": deepcopy(atomic_payload), "anchor_offset_days": 0},
            {"role": "previous", "metric": deepcopy(atomic_payload), "anchor_offset_days": 30},
        ],
        "expression": {"operator": "growth_rate"},
        "zero_division": {"strategy": "null"},
    }


def request_payload(requirement="近30天企业开票金额"):
    return {
        "requirement": requirement,
        "scenario": "invoice_risk",
        "execution_context": {"anchor_time": "2026-09-09T00:00:00+08:00"},
    }


def llm_for(payload):
    return MockLLMClient(json.dumps({"metric_ir": payload, "unsupported_reason": None}))


def count_payload(atomic):
    return atomic | {
        "name": "invoice_count_30d",
        "display_name": "近30天企业开票次数",
        "description": "企业近30日开票原始记录行数",
        "aggregation": {"function": "count", "field": None},
    }


@pytest.mark.parametrize("kind", ["amount", "count", "growth"])
def test_three_generate_apis_persist_validated_drafts(
    settings, atomic_payload, growth_payload, kind
):
    payload = {
        "amount": atomic_payload,
        "count": count_payload(atomic_payload),
        "growth": growth_payload,
    }[kind]
    app = create_app(settings, llm_client=llm_for(payload))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/development/generate", json=request_payload(payload["display_name"])
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["validation"]["valid"]
        assert result["artifact"]["status"] == "draft"
        assert result["requires_human_review"] is True
        assert result["workflow_run_id"] != result["artifact"]["artifact_id"]
        names = [item["name"] for item in result["skill_plan"]["capabilities"]]
        if kind == "count":
            assert "metric_count" in names and "metric_sum" not in names
            assert "COUNT(*)" in result["artifact"]["code"]
            assert "DISTINCT" not in result["artifact"]["code"]
        if kind == "growth":
            assert "metric_growth_rate" in names and "metric_count" not in names
            assert len(result["derived_plan"]["base_metrics"]) == 2
        with app.state.database.session_factory() as session:
            row = session.get(DevelopmentArtifactRow, result["artifact"]["artifact_id"])
            assert row.status == "draft"
            assert row.workflow_run_id == result["workflow_run_id"]
        review = client.post("/api/v1/approvals", json=submit_payload(result))
        assert review.status_code == 201
        approval = client.post(
            f"/api/v1/approvals/{review.json()['approval_id']}/approve",
            json={"reviewer": "iris", "comment": "人工确认该版本"},
        )
        assert approval.status_code == 200
        artifact_view = client.get(
            "/api/v1/development/artifacts/" + result["artifact"]["artifact_id"]
        )
        assert artifact_view.json()["status"] == "approved"


def generate(client):
    response = client.post("/api/v1/development/generate", json=request_payload())
    assert response.status_code == 200, response.text
    return response.json()


def submit_payload(result):
    return {
        "artifact_id": result["artifact"]["artifact_id"],
        "artifact_hash": result["artifact"]["content_hash"],
        "metric_ir_hash": canonical_hash(result["metric_ir"]),
    }


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_lifecycle_auditable_and_terminal(settings, atomic_payload, decision):
    app = create_app(settings, llm_client=llm_for(atomic_payload))
    with TestClient(app) as client:
        generated = generate(client)
        submitted = client.post("/api/v1/approvals", json=submit_payload(generated))
        assert submitted.status_code == 201
        pending = submitted.json()
        assert pending["decision"] == "pending"
        assert pending["reviewer"] is None
        review_url = "/api/v1/approvals/" + pending["approval_id"]
        assert client.get(review_url).json() == pending
        response = client.post(
            review_url + "/" + decision, json={"reviewer": "iris", "comment": "口径检查完成"}
        )
        assert response.status_code == 200
        record = response.json()
        assert record["decision"] == ("approved" if decision == "approve" else "rejected")
        assert record["reviewer"] == "iris" and record["reviewed_at"] is not None
        assert record["artifact_version"] == generated["artifact"]["artifact_version"]
        assert record["workflow_run_id"] == generated["workflow_run_id"]
        assert record["artifact_hash"] == generated["artifact"]["content_hash"]
        duplicate = client.post(
            review_url + "/approve", json={"reviewer": "other", "comment": "again"}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "approval_invalid_state"
        assert client.get(review_url).json() == record
        assert client.post("/api/v1/approvals", json=submit_payload(generated)).status_code == 409
        with app.state.database.session_factory() as session:
            service = ApprovalService(session)
            if decision == "approve":
                service.require_approved(record["approval_id"], record["artifact_id"])
            else:
                with pytest.raises(ApprovalStateError):
                    service.require_approved(record["approval_id"], record["artifact_id"])
        # A new draft starts a fresh cycle; no old review is overwritten.
        fresh = generate(client)
        assert fresh["artifact"]["artifact_id"] != generated["artifact"]["artifact_id"]
        assert client.post("/api/v1/approvals", json=submit_payload(fresh)).status_code == 201
        with app.state.database.session_factory() as session:
            assert len(session.scalars(select(ApprovalRecordRow)).all()) == 2


@pytest.mark.parametrize("field", ["code", "metric_ir", "content_hash"])
def test_changed_content_cannot_be_approved(settings, atomic_payload, field):
    app = create_app(settings, llm_client=llm_for(atomic_payload))
    with TestClient(app) as client:
        generated = generate(client)
        pending = client.post("/api/v1/approvals", json=submit_payload(generated)).json()
        with app.state.database.session_factory() as session:
            row = session.get(DevelopmentArtifactRow, generated["artifact"]["artifact_id"])
            snapshot = deepcopy(row.snapshot)
            if field == "metric_ir":
                snapshot["metric_ir"]["description"] = "different semantics"
            else:
                snapshot["artifact"][field] = "SELECT 1" if field == "code" else "0" * 64
            row.snapshot = snapshot
            session.commit()
        response = client.post(
            f"/api/v1/approvals/{pending['approval_id']}/approve",
            json={"reviewer": "iris", "comment": "review"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "approval_artifact_changed"


def test_approved_gate_rechecks_content_and_identity(settings, atomic_payload):
    app = create_app(settings, llm_client=llm_for(atomic_payload))
    with TestClient(app) as client:
        result = generate(client)
        pending = client.post("/api/v1/approvals", json=submit_payload(result)).json()
        client.post(
            f"/api/v1/approvals/{pending['approval_id']}/approve",
            json={"reviewer": "iris", "comment": "review"},
        )
        with app.state.database.session_factory() as session:
            service = ApprovalService(session)
            with pytest.raises(ApprovalArtifactChangedError):
                service.require_approved(pending["approval_id"], "another-artifact")
            row = session.get(DevelopmentArtifactRow, pending["artifact_id"])
            altered = deepcopy(row.snapshot)
            altered["artifact"]["code"] += " -- changed"
            row.snapshot = altered
            session.commit()
            with pytest.raises(ApprovalArtifactChangedError):
                service.require_approved(pending["approval_id"], pending["artifact_id"])


def test_canonical_hash_key_order_identity_and_mutation(settings, atomic_payload):
    assert canonical_hash(atomic_payload) == canonical_hash(
        dict(reversed(list(atomic_payload.items())))
    )
    app = create_app(settings, llm_client=llm_for(atomic_payload))
    workflow = app.state.development_workflow
    request = DevelopmentRequest.model_validate(request_payload())
    first, second = workflow.run(request), workflow.run(request)
    assert first.workflow_run_id != second.workflow_run_id
    assert first.artifact.artifact_id != second.artifact.artifact_id
    assert first.artifact.content_hash == second.artifact.content_hash
    assert (
        artifact_content_hash(first.artifact.model_copy(update={"code": "SELECT 1"}))
        != first.artifact.content_hash
    )


def test_approval_unknown_and_bad_schema(settings, atomic_payload):
    with TestClient(create_app(settings, llm_client=llm_for(atomic_payload))) as client:
        assert client.get("/api/v1/approvals/missing").status_code == 404
        result = generate(client)
        wrong = submit_payload(result) | {"artifact_hash": "0" * 64}
        assert client.post("/api/v1/approvals", json=wrong).status_code == 409
        pending = client.post("/api/v1/approvals", json=submit_payload(result)).json()
        response = client.post(
            f"/api/v1/approvals/{pending['approval_id']}/approve",
            json={"reviewer": "", "comment": ""},
        )
        assert response.status_code == 422
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_migration_upgrade_indexes_and_downgrade(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        metadata = inspect(connection)
        assert {"development_artifacts", "approval_records"}.issubset(metadata.get_table_names())
        assert any(
            item["column_names"] == ["artifact_id"]
            for item in metadata.get_unique_constraints("approval_records")
        )
        assert any(
            item["name"] == "ix_approval_decision_created"
            for item in metadata.get_indexes("approval_records")
        )
        assert metadata.get_foreign_keys("approval_records")
        command.downgrade(config, "base")
        assert "approval_records" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
    engine.dispose()


def test_growth_windows_and_relational_semantics(settings, growth_payload):
    app = create_app(settings, llm_client=llm_for(growth_payload))
    result = app.state.development_workflow.run(
        DevelopmentRequest.model_validate(request_payload())
    )
    code = result.artifact.code
    assert "2026-07-11" in code and "2026-08-10" in code and "2026-09-09" in code
    assert "FULL OUTER JOIN" in code and "THEN NULL" in code
    assert any("previous period amount = 0" in warning for warning in result.artifact.warnings)
    # SQLite relational oracle only: adapt DATE literals and Spark null-safe equality.
    # This does not validate Spark execution or Spark types.
    portable = re.sub(r"DATE '([^']+)'", r"'\1'", code).replace("<=>", "IS")
    connection = sqlite3.connect(":memory:")
    connection.execute("ATTACH DATABASE ':memory:' AS c_db")
    connection.execute(
        "CREATE TABLE c_db.source_fp_jdc_view "
        "(seller_tax_no TEXT, invoice_amt REAL, invoice_date TEXT)"
    )
    rows = [
        ("up", 150, 100),
        ("down", 50, 100),
        ("same", 100, 100),
        ("zero", 100, 0),
        ("current_only", 100, None),
        ("previous_only", None, 100),
    ]
    for entity, current, previous in rows:
        if current is not None:
            connection.execute(
                "INSERT INTO c_db.source_fp_jdc_view VALUES (?, ?, ?)",
                (entity, current, "2026-08-10"),
            )
        if previous is not None:
            connection.execute(
                "INSERT INTO c_db.source_fp_jdc_view VALUES (?, ?, ?)",
                (entity, previous, "2026-07-11"),
            )
    # Right-open boundary must not contribute.
    connection.execute("INSERT INTO c_db.source_fp_jdc_view VALUES ('up',9999,'2026-09-09')")
    values = {row[0]: row[3] for row in connection.execute(portable)}
    assert values == {
        "up": 0.5,
        "down": -0.5,
        "same": 0,
        "zero": None,
        "current_only": None,
        "previous_only": -1,
    }
    connection.close()


@pytest.mark.parametrize(
    "before,after",
    [
        ("FULL OUTER JOIN", "INNER JOIN"),
        ("THEN NULL", "THEN 0"),
        ("2026-07-11", "2026-07-12"),
        ("invoice_amt", "other_amt"),
        ("1.0 *", "evil_udf(1) *"),
        (";", "; DROP TABLE x;"),
    ],
)
def test_derived_validator_rejects_tampering(settings, growth_payload, before, after):
    app = create_app(settings, llm_client=llm_for(growth_payload))
    result = app.state.development_workflow.run(
        DevelopmentRequest.model_validate(request_payload())
    )
    report = DerivedSQLValidator().validate(
        result.artifact.code.replace(before, after),
        "spark_sql",
        result.metric_ir,
        result.execution_context,
    )
    assert not report.valid


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("operator", "unsupported_derived_metric"),
        ("zero", "zero_division_strategy_invalid"),
        ("offset", "derived_metric_dependency_invalid"),
    ],
)
def test_derived_explicit_errors(settings, growth_payload, kind, expected):
    if kind == "operator":
        growth_payload["expression"]["operator"] = "ratio"
    elif kind == "zero":
        growth_payload["zero_division"]["strategy"] = "zero"
    else:
        growth_payload["dependencies"][1]["anchor_offset_days"] = 0
    with TestClient(create_app(settings, llm_client=llm_for(growth_payload))) as client:
        response = client.post("/api/v1/development/generate", json=request_payload())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == expected


@pytest.mark.parametrize(
    "stage,code",
    [
        ("planner", "planning_failed"),
        ("tool", "tool_output_invalid"),
        ("validator", "sql_validation_failed"),
        ("unsupported", "unsupported_metric"),
        ("parser", "requirement_parse_failed"),
    ],
)
def test_api_stages_fail_without_persisting_draft(
    settings, atomic_payload, stage, code, monkeypatch
):
    app = create_app(settings, llm_client=llm_for(atomic_payload))
    workflow = app.state.development_workflow
    if stage == "planner":
        monkeypatch.setattr(
            workflow.skill_planner, "plan", Mock(side_effect=PlanningError("missing"))
        )
    elif stage == "tool":
        monkeypatch.setattr(
            app.state.tools.get("generate_spark_sql_metric", "1.1.0"),
            "invoke",
            Mock(side_effect=ToolOutputError("bad")),
        )
    elif stage == "validator":
        # The validator is chosen by the metric's shape, so the seam is the
        # dispatcher rather than a workflow attribute.
        monkeypatch.setattr(
            "airi.workflows.development.workflow.validator_for_metric",
            Mock(return_value=Mock(validate=Mock(side_effect=SQLValidationError("bad")))),
        )
    elif stage == "unsupported":
        atomic_payload["source"]["database"] = "unknown"
        workflow.parser.llm = llm_for(atomic_payload)
    else:
        workflow.parser.llm = MockLLMClient("bad json")
    with TestClient(app) as client:
        response = client.post("/api/v1/development/generate", json=request_payload())
        assert response.json()["error"]["code"] == code
        with app.state.database.session_factory() as session:
            assert session.scalars(select(DevelopmentArtifactRow)).all() == []

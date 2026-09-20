import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from airi.approvals.artifacts import canonical_hash
from airi.approvals.persistence import DevelopmentArtifactRow
from airi.core.execution import ExecutionContext
from airi.execution.models import (
    ExecutionPlan,
    ExecutionProfile,
    ExecutionRequest,
    ExecutionResult,
    QueryOutput,
    ResultColumn,
)
from airi.execution.persistence import ExecutionRunRow
from airi.execution.sandbox import SandboxGuard, SandboxViolation
from airi.infrastructure.llm import MockLLMClient
from airi.infrastructure.query_executor import (
    MockQueryExecutor,
    QueryFailure,
    classify_provider_error,
    run_cursor,
)
from airi.main import create_app
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR
from airi.testing.models import ScenarioTestRules
from airi.testing.planning import MetricTestPlanner
from airi.testing.reconciliation import reconciles, reference_calculate
from airi.testing.runner import MetricTestRunner, boundary_matches

NAMES = ["invoice_amount_30d", "invoice_count_30d", "invoice_amount_growth_30d"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def invoices():
    return read("tests/fixtures/invoices.json")


def metric(name):
    return TypeAdapter(MetricIR | DerivedMetricIR).validate_python(read(f"examples/{name}_ir.json"))


def plan():
    return ExecutionPlan(
        artifact_id="artifact",
        approval_id="approval",
        workflow_run_id="workflow",
        request_id="request",
        execution_context=ExecutionContext.model_validate(
            {"anchor_time": "2026-09-09T00:00:00+08:00"}
        ),
        sql_hash="a" * 64,
    )


def execution(name, invoices):
    code = Path(f"examples/{name}.sql").read_text(encoding="utf-8")
    output = MockQueryExecutor(invoices).execute(code, plan())
    return ExecutionResult(
        **plan().model_dump(),
        status="success",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=1.0,
        row_count=len(output.rows),
        **output.model_dump(),
    ), code


def report(result, code, name, invoices):
    rules = ScenarioTestRules()
    test_plan = MetricTestPlanner().plan(result.artifact_id, result.execution_run_id, rules)
    return MetricTestRunner().run(test_plan, result, metric(name), code, rules, invoices)


def generate(client, name):
    response = client.post(
        "/api/v1/development/generate", json=read(f"examples/{name}_request.json")
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, generated):
    response = client.post(
        "/api/v1/approvals",
        json={
            "artifact_id": generated["artifact"]["artifact_id"],
            "artifact_hash": generated["artifact"]["content_hash"],
            "metric_ir_hash": canonical_hash(generated["metric_ir"]),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["approval_id"]


def approve_fixture(client, approval_id, **kwargs):
    response = client.post(f"/api/v1/approvals/{approval_id}/approve", **kwargs)
    assert response.status_code == 200, response.text
    return response


def app_for(settings, name, executor, **extra):
    return create_app(
        settings,
        llm_client=MockLLMClient(
            json.dumps({"metric_ir": read(f"examples/{name}_ir.json"), "unsupported_reason": None})
        ),
        query_executor=executor,
        **extra,
    )


@pytest.mark.parametrize("name", NAMES)
def test_end_to_end(settings, invoices, name):
    app = app_for(settings, name, MockQueryExecutor(invoices))
    with TestClient(app) as client:
        generated = generate(client, name)
        approval_id = submit(client, generated)
        approved = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"reviewer": "fixture-reviewer", "comment": "Test only"},
        )
        assert approved.status_code == 200
        request = {"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id}
        response = client.post("/api/v1/tests/run", json=request)
        assert response.status_code == 200, response.text
        data = response.json()
        assert response.headers["X-Request-ID"] == data["execution"]["request_id"]
        assert data["execution"]["status"] == "success"
        assert data["report"]["total"] == 6
        assert data["report"]["status"] == "passed_with_warnings"
        assert data["report"]["passed"] == 5
        assert data["report"]["warnings"] == 1
        assert client.get(f"/api/v1/tests/{data['report']['test_run_id']}").json() == data["report"]
        assert client.post("/api/v1/executions", json=request).status_code == 200
        with app.state.database.session_factory() as session:
            row = session.scalars(select(ExecutionRunRow)).first()
            assert "rows" not in row.metadata_json


@pytest.mark.parametrize("state", ["draft", "pending", "rejected"])
def test_unapproved_blocked(settings, invoices, state):
    executor = MockQueryExecutor(invoices)
    executor.execute = Mock(wraps=executor.execute)
    with TestClient(app_for(settings, NAMES[0], executor)) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        if state == "rejected":
            client.post(
                f"/api/v1/approvals/{approval_id}/reject",
                json={"reviewer": "test", "comment": "Fixture review"},
            )
        if state == "draft":
            with client.app.state.database.session_factory() as session:
                row = session.get(DevelopmentArtifactRow, generated["artifact"]["artifact_id"])
                row.status = "draft"
                session.commit()
        request = {"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id}
        for endpoint in ("/api/v1/executions", "/api/v1/tests/run"):
            assert client.post(endpoint, json=request).status_code == 409
        executor.execute.assert_not_called()


@pytest.mark.parametrize("field", ["code", "metric_ir", "anchor"])
def test_tampered_approved_blocked(settings, invoices, field):
    executor = MockQueryExecutor(invoices)
    executor.execute = Mock(wraps=executor.execute)
    app = app_for(settings, NAMES[0], executor)
    with TestClient(app) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        approve_fixture(client, approval_id, json={"reviewer": "test", "comment": "Fixture review"})
        with app.state.database.session_factory() as session:
            row = session.get(DevelopmentArtifactRow, generated["artifact"]["artifact_id"])
            snapshot = deepcopy(row.snapshot)
            if field == "code":
                snapshot["artifact"]["code"] += " DROP TABLE x"
            elif field == "metric_ir":
                snapshot["metric_ir"]["name"] = "tampered"
            else:
                snapshot["execution_context"]["anchor_time"] = "2026-09-10T00:00:00+08:00"
            row.snapshot = snapshot
            session.commit()
        response = client.post(
            "/api/v1/tests/run",
            json={"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id},
        )
        assert response.status_code in (403, 409)
        executor.execute.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"sql": "SELECT 1"},
        {"execution_profile": "production"},
        {"max_rows": 1001},
        {"max_rows": 0},
        {"timeout_seconds": 61},
        {"timeout_seconds": 0},
        {"max_rows": True},
    ],
)
def test_api_rejects_unsafe_input(client, change):
    response = client.post(
        "/api/v1/executions", json={"artifact_id": "a", "approval_id": "b", **change}
    )
    assert response.status_code == 422
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "MERGE",
        "REPLACE",
        "LOAD",
        "EXPORT",
        "IMPORT",
        "CACHE TABLE",
        "UNCACHE",
        "REFRESH",
        "SET",
        "ADD JAR",
        "ADD FILE",
        "DFS",
        "reflect",
        "java_method",
        "xpath",
        "input_file_name",
        "SELECT 1; SELECT 2",
    ],
)
def test_sandbox_dangerous(statement):
    with pytest.raises(SandboxViolation):
        SandboxGuard().validate(
            statement, metric(NAMES[0]), plan().execution_context, ExecutionProfile()
        )


@pytest.mark.parametrize("name", NAMES)
def test_sandbox_valid_and_boundary(name):
    code = Path(f"examples/{name}.sql").read_text(encoding="utf-8")
    SandboxGuard().validate(code, metric(name), plan().execution_context, ExecutionProfile())
    assert boundary_matches(code, metric(name), plan().execution_context)
    assert not boundary_matches(
        code.replace(">= DATE", "> DATE"), metric(name), plan().execution_context
    )
    assert not boundary_matches(
        code.replace("< DATE", "<= DATE"), metric(name), plan().execution_context
    )


@pytest.mark.parametrize("name", NAMES)
def test_reconciliation_detects_changed_value(name, invoices):
    result, code = execution(name, invoices)
    assert report(result, code, name, invoices).failed == 0
    changed = deepcopy(result.rows)
    changed[0][name] = 999
    modified = result.model_copy(update={"rows": changed})
    assert (
        report(modified, code, name, invoices).results[-1].failure_category
        == "reconciliation_mismatch"
    )


@pytest.mark.parametrize("mutation", ["missing_entity", "missing_value", "extra", "type"])
def test_schema_rejects(invoices, mutation):
    result, code = execution(NAMES[0], invoices)
    columns = list(result.columns)
    if mutation == "missing_entity":
        columns.pop(0)
    elif mutation == "missing_value":
        columns.pop()
    elif mutation == "extra":
        columns.append(ResultColumn(name="extra", type="string"))
    else:
        columns[1] = ResultColumn(name=NAMES[0], type="string")
    result = result.model_copy(update={"columns": columns})
    assert report(result, code, NAMES[0], invoices).results[0].status == "failed"


@pytest.mark.parametrize("nulls", [0, 1, 2])
def test_null_and_duplicate(invoices, nulls):
    result, code = execution(NAMES[0], invoices)
    rows = [{"entity_id": None, NAMES[0]: 1}] * nulls + [{"entity_id": "A", NAMES[0]: 10}]
    result = result.model_copy(update={"rows": rows, "row_count": len(rows)})
    checks = report(result, code, NAMES[0], None).results
    assert checks[1].status == ("warning" if nulls else "passed")
    assert checks[2].status == ("failed" if nulls > 1 else "passed")


def test_empty_and_truncated(invoices):
    result, code = execution(NAMES[0], invoices)
    empty = result.model_copy(update={"rows": [], "row_count": 0})
    assert report(empty, code, NAMES[0], invoices).status == "passed_with_warnings"
    truncated = result.model_copy(update={"truncated": True})
    assert report(truncated, code, NAMES[0], invoices).results[-1].status == "skipped"


@pytest.mark.parametrize(
    "message,category",
    [
        ("syntax error SECRET", "syntax_error"),
        ("unresolved_column SECRET", "schema_error"),
        ("timeout SECRET", "timeout"),
        ("permission denied SECRET", "permission_error"),
        ("type mismatch SECRET", "data_type_error"),
        ("out of memory SECRET", "resource_limit"),
        ("SECRET", "unknown"),
    ],
)
def test_provider_classification(message, category):
    assert classify_provider_error(RuntimeError(message)) == category


def test_adapter_cursor_is_bounded():
    cursor = Mock()
    cursor.poll.return_value = SimpleNamespace(operationState=2)
    cursor.description = [("entity_id", "STRING"), (NAMES[0], "DECIMAL")]
    cursor.fetchmany.return_value = [("A", 1)]
    connection = Mock()
    connection.cursor.return_value = cursor
    output = run_cursor(connection, "approved_sql", plan())
    assert len(output.rows) == 1
    cursor.execute.assert_called_once_with("approved_sql", async_=True)
    cursor.fetchmany.assert_called_once_with(101)
    cursor.close.assert_called_once()


def test_adapter_cancels_failure():
    cursor = Mock()
    cursor.poll.return_value = SimpleNamespace(operationState=5)
    connection = Mock()
    connection.cursor.return_value = cursor
    with pytest.raises(QueryFailure):
        run_cursor(connection, "approved_sql", plan())
    cursor.cancel.assert_called_once()


def test_strict_models():
    with pytest.raises(ValidationError):
        ExecutionProfile(environment="production")
    with pytest.raises(ValidationError):
        ExecutionRequest(artifact_id="a", approval_id="b", sql="SELECT 1")
    with pytest.raises(ValidationError):
        QueryOutput(columns=[], rows=[{"x": "x" * 4097}])


def test_reference_growth_cases(invoices):
    values = {
        r["entity_id"]: r for r in reference_calculate(NAMES[2], plan().execution_context, invoices)
    }
    assert values["A"][NAMES[2]] == 2
    assert values["B"][NAMES[2]] < 0
    assert values["C"][NAMES[2]] == 0
    assert values["CURRENT_ONLY"][NAMES[2]] is None
    assert values["PREVIOUS_ONLY"][NAMES[2]] == -1
    assert values["ZERO"][NAMES[2]] is None
    assert not reconciles([{"entity_id": "A", "x": "bad"}], [{"entity_id": "A", "x": 1}])


@pytest.mark.parametrize("failure", ["timeout", "schema_error", "syntax_error", "unknown"])
def test_execution_failure_is_safe_and_persisted(settings, failure):
    executor = MockQueryExecutor(failure=failure)
    with TestClient(app_for(settings, NAMES[0], executor)) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        approve_fixture(client, approval_id, json={"reviewer": "test", "comment": "Fixture review"})
        response = client.post(
            "/api/v1/tests/run",
            json={"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["execution"]["failure_category"] == failure
        assert result["execution"]["status"] == ("timeout" if failure == "timeout" else "failed")
        assert result["report"]["status"] == "failed"
        assert result["report"]["skipped"] == 6
        assert not result["execution"]["rows"]
        with client.app.state.database.session_factory() as session:
            row = session.get(ExecutionRunRow, result["execution"]["execution_run_id"])
            assert row.failure_category == failure


def test_limit_rows_and_report_not_found(settings, invoices):
    with TestClient(app_for(settings, NAMES[0], MockQueryExecutor(invoices))) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        approve_fixture(client, approval_id, json={"reviewer": "test", "comment": "Fixture review"})
        result = client.post(
            "/api/v1/tests/run",
            json={
                "artifact_id": generated["artifact"]["artifact_id"],
                "approval_id": approval_id,
                "max_rows": 1,
            },
        ).json()
        assert result["execution"]["row_count"] == 1
        assert result["execution"]["truncated"]
        assert result["report"]["results"][-1]["status"] == "skipped"
        missing = client.get("/api/v1/tests/missing")
        assert missing.status_code == 404
        assert "test_report_not_found" in missing.text


def test_provider_exception_never_exposed(settings):
    executor = MockQueryExecutor()
    executor.execute = Mock(side_effect=RuntimeError("secret password and SQL"))
    with TestClient(app_for(settings, NAMES[0], executor)) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        approve_fixture(client, approval_id, json={"reviewer": "test", "comment": "Fixture review"})
        response = client.post(
            "/api/v1/executions",
            json={"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id},
        )
        assert "secret" not in response.text
        assert response.json()["failure_category"] == "unknown"


@pytest.mark.parametrize("database", ["prod_sensitive_db", "unknown_db"])
def test_database_allowlist(database):
    ir = metric(NAMES[0])
    ir = ir.model_copy(update={"source": ir.source.model_copy(update={"database": database})})
    with pytest.raises(SandboxViolation):
        SandboxGuard().validate("SELECT 1", ir, plan().execution_context, ExecutionProfile())


@pytest.mark.parametrize("name", NAMES)
def test_window_tamper_classified(name, invoices):
    result, code = execution(name, invoices)
    changed = code.replace("2026-08-10", "2026-08-11")
    assert report(result, changed, name, invoices).results[3].failure_category == "window_mismatch"


def test_schema_and_empty_mock_api(settings):
    output = QueryOutput(columns=[ResultColumn(name="wrong", type="string")], rows=[])
    with TestClient(app_for(settings, NAMES[0], MockQueryExecutor(output=output))) as client:
        generated = generate(client, NAMES[0])
        approval_id = submit(client, generated)
        approve_fixture(client, approval_id, json={"reviewer": "test", "comment": "Fixture review"})
        result = client.post(
            "/api/v1/tests/run",
            json={"artifact_id": generated["artifact"]["artifact_id"], "approval_id": approval_id},
        ).json()
        assert result["execution"]["status"] == "success"
        assert result["report"]["results"][0]["failure_category"] == "schema_error"
        assert result["report"]["results"][1]["failure_category"] == "empty_result"


def test_adapter_timeout_cancels(monkeypatch):
    cursor = Mock()
    cursor.poll.return_value = SimpleNamespace(operationState=1)
    connection = Mock()
    connection.cursor.return_value = cursor
    times = iter([0, 61])
    monkeypatch.setattr("airi.infrastructure.query_executor.time.monotonic", lambda: next(times))
    with pytest.raises(QueryFailure, match="timeout"):
        run_cursor(connection, "approved_sql", plan())
    cursor.cancel.assert_called_once()


def test_all_passed_without_null(invoices):
    source = [r for r in invoices if r["seller_tax_no"] is not None]
    result, code = execution(NAMES[0], source)
    assert report(result, code, NAMES[0], source).status == "passed"


@pytest.mark.parametrize("outcome", ["success", "timeout", "failure"])
def test_spark_process_boundary(settings, monkeypatch, outcome):
    from airi.infrastructure.query_executor import SparkSQLExecutor

    settings.spark_test_host = "test.invalid"
    settings.spark_test_username = "test-reader"
    receiver, sender, process = Mock(), Mock(), Mock()
    receiver.poll.return_value = outcome != "timeout"
    receiver.recv.return_value = (
        ("failure", "schema_error")
        if outcome == "failure"
        else ("success", QueryOutput(columns=[], rows=[]).model_dump_json())
    )
    context = Mock()
    context.Pipe.return_value = (receiver, sender)
    context.Process.return_value = process
    monkeypatch.setattr(
        "airi.infrastructure.query_executor.multiprocessing.get_context", Mock(return_value=context)
    )
    if outcome == "success":
        assert SparkSQLExecutor(settings).execute("approved", plan()).rows == []
    else:
        with pytest.raises(QueryFailure):
            SparkSQLExecutor(settings).execute("approved", plan())
    receiver.poll.assert_called_once_with(60)
    sender.close.assert_called_once()
    receiver.close.assert_called_once()
    process.terminate.assert_called_once()


def test_report_rejects_inconsistent_status(invoices):
    from airi.testing.models import MetricTestReport

    result, code = execution(NAMES[0], invoices)
    data = report(result, code, NAMES[0], invoices).model_dump()
    data["status"] = "passed"
    with pytest.raises(ValidationError):
        MetricTestReport.model_validate(data)

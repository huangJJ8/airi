from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from airi.core.config import Settings
from airi.environments.fixtures import FixtureSourceMapping, fixture_rows
from airi.environments.metadata import (
    MetadataFailure,
    MetricMetadataValidator,
    SchemaInspector,
    fixture_matches,
)
from airi.environments.persistence import EnvironmentValidationRow
from airi.environments.probes import CAPABILITIES, probe_sql
from airi.environments.service import SparkEnvironmentProbe, integration_metrics, metric_sql
from airi.execution.models import QueryOutput
from airi.infrastructure.query_executor import MockQueryExecutor, QueryFailure, SparkSQLExecutor
from airi.infrastructure.result_normalizer import ResultNormalizer
from airi.main import create_app
from airi.testing.reconciliation import reconciles, reference_calculate


def output(rows, truncated=False):
    return QueryOutput(columns=[], rows=rows, truncated=truncated)


def schema_output(time_type="date", amount_type="decimal(18,2)"):
    return output(
        [
            {"col_name": name, "data_type": kind, "comment": None}
            for name, kind in (
                ("seller_tax_no", "string"),
                ("invoice_amt", amount_type),
                ("invoice_date", time_type),
                ("dt", "string"),
            )
        ]
    )


class FakeSpark(MockQueryExecutor):
    def __init__(self, timezone="Asia/Shanghai", fail=None):
        super().__init__(fixture_rows())
        self.timezone, self.fail = timezone, fail

    def probe(self, name):
        if name == self.fail:
            raise QueryFailure("connection_error")
        if name == "session":
            return output([{"engine_version": "unit-test-only", "session_timezone": self.timezone}])
        if name in CAPABILITIES:
            return output([{"value": CAPABILITIES[name][1]}])
        if name in ("source_schema", "fixture_schema"):
            return schema_output()
        if name == "decimal":
            return output([{"value": "0.30", "result_type": "decimal(28,2)"}])
        if name == "resource":
            return output([{"value": n} for n in range(10)], True)
        if name == "cancel":
            return QueryOutput(columns=[], rows=[], cancel_requested=True)
        if name == "fixture_rows":
            return output(fixture_rows())
        raise AssertionError(name)


@pytest.mark.parametrize("mode", ["disabled", "mock", "spark_test"])
def test_modes(mode):
    settings = Settings(_env_file=None, execution_mode=mode)
    assert settings.effective_execution_mode == mode
    assert settings.spark_password == SecretStr("")
    assert create_app(settings).state.query_executor is not None


def test_missing_config_fails_closed():
    from test_phase2 import plan

    with pytest.raises(QueryFailure):
        SparkSQLExecutor(Settings(_env_file=None)).execute("unused", plan())


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("123456789012345678.123456789"), "123456789012345678.123456789"),
        (date(2026, 9, 9), "2026-09-09"),
        (datetime(2026, 9, 9, 1, 2), "2026-09-09T01:02:00"),
        (None, None),
        (1, 1),
        (1.5, 1.5),
    ],
)
def test_normalization(value, expected):
    normalized = ResultNormalizer.cell(value)
    assert normalized == expected
    assert (
        QueryOutput.model_validate_json(output([{"value": normalized}]).model_dump_json()).rows[0][
            "value"
        ]
        == expected
    )


@pytest.mark.parametrize("value", [float("inf"), Decimal("NaN"), object()])
def test_invalid_values_rejected(value):
    with pytest.raises(ValueError):
        ResultNormalizer.cell(value)


@pytest.mark.parametrize(
    "time_type,amount_type,timezone,error",
    [
        ("date", "decimal(18,2)", "Asia/Shanghai", None),
        ("timestamp", "double", "Asia/Shanghai", None),
        ("string", "decimal(18,2)", "Asia/Shanghai", "unsupported_source_schema"),
        ("date", "string", "Asia/Shanghai", "unsupported_source_schema"),
        ("timestamp", "decimal(18,2)", "UTC", "timezone_mismatch"),
    ],
)
def test_metadata(time_type, amount_type, timezone, error):
    schema = SchemaInspector().inspect(schema_output(time_type, amount_type))
    if error:
        with pytest.raises(MetadataFailure, match=error):
            MetricMetadataValidator().validate(schema, timezone)
    else:
        MetricMetadataValidator().validate(schema, timezone)


def test_missing_table_field():
    with pytest.raises(MetadataFailure):
        SchemaInspector().inspect(output([]))
    schema = SchemaInspector().inspect(output(schema_output().rows[:-1]))
    with pytest.raises(MetadataFailure):
        MetricMetadataValidator().validate(schema, "Asia/Shanghai")


@pytest.mark.parametrize("name", ["SHOW DATABASES", "DROP", "host", "fixture_rows"])
def test_probe_allowlist(name):
    with pytest.raises(ValueError):
        probe_sql(name)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_fixture_parity(index):
    metric = integration_metrics()[index]
    code, context = metric_sql(metric)
    actual = MockQueryExecutor.evaluate(code, fixture_rows()).rows
    assert reconciles(actual, reference_calculate(metric.name, context, fixture_rows()))
    assert "tmp_db" not in metric.model_dump_json()
    assert "tmp_db" in FixtureSourceMapping(enabled=True).apply(code)
    assert fixture_matches(output(fixture_rows()))
    assert not fixture_matches(output(fixture_rows()[:-1]))


@pytest.mark.parametrize(
    "timezone,fail,expected",
    [
        ("Asia/Shanghai", None, "passed_with_warnings"),
        ("UTC", None, "failed"),
        ("Asia/Shanghai", "session", "failed"),
        ("Asia/Shanghai", "cte", "failed"),
        ("Asia/Shanghai", "source_schema", "failed"),
    ],
)
def test_environment_report(timezone, fail, expected):
    settings = Settings(_env_file=None, spark_fixture_mapping=True)
    report = SparkEnvironmentProbe(FakeSpark(timezone, fail), settings).validate()
    assert report.overall == expected
    assert any(
        e.evidence_type == "client_timeout" and e.status == "not_verified" for e in report.evidence
    )
    assert report.requires_human_review


def test_environment_api_and_persistence(settings):
    settings.execution_mode = "spark_test"
    app = create_app(settings, query_executor=FakeSpark())
    with TestClient(app) as client:
        response = client.get("/api/v1/environments/spark-test/status")
        assert response.status_code == 200
        assert response.json()["reachable"]
        result = client.post("/api/v1/environments/spark-test/validate", json={})
        assert result.status_code == 200, result.text
        assert result.headers["X-Request-ID"]
        with app.state.database.session_factory() as session:
            row = session.scalars(select(EnvironmentValidationRow)).one()
            assert row.report_json["validation_run_id"] == result.json()["validation_run_id"]
        assert (
            client.post(
                "/api/v1/environments/spark-test/validate", json={"host": "secret"}
            ).status_code
            == 422
        )


def test_environment_api_disabled(client):
    assert client.get("/api/v1/environments/spark-test/status").status_code == 403
    assert client.get("/health").status_code == 200


def test_decimal_description():
    assert ResultNormalizer.type_name(("x", "DECIMAL", None, None, 28, 2)) == "decimal(28,2)"


@pytest.mark.parametrize("unsupported", [False, True])
def test_real_worker_preflight_without_provider_dependency(monkeypatch, unsupported):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock

    from test_phase2 import plan

    from airi.infrastructure.query_executor import _spark_worker

    connection, socket, pipe = Mock(), Mock(), Mock()
    factory = Mock(return_value=connection)
    monkeypatch.setitem(
        sys.modules, "pyhive", SimpleNamespace(hive=SimpleNamespace(Connection=factory))
    )
    monkeypatch.setitem(
        sys.modules,
        "thrift.transport",
        SimpleNamespace(
            TSocket=SimpleNamespace(TSocket=Mock(return_value=socket)),
            TTransport=SimpleNamespace(TBufferedTransport=Mock()),
        ),
    )
    monkeypatch.setattr("airi.infrastructure.query_executor.logging.disable", Mock())
    metric = integration_metrics()[0]
    code, _ = metric_sql(metric)

    def cursor_output(conn, sql, execution_plan):
        for name, (query, expected) in CAPABILITIES.items():
            if sql == query:
                return output([{"value": expected}])
        if sql == probe_sql("session"):
            return output([{"engine_version": "unit", "session_timezone": "Asia/Shanghai"}])
        if sql == probe_sql("fixture_schema", True):
            return schema_output("string" if unsupported else "date")
        if sql == probe_sql("fixture_rows", True):
            return output(fixture_rows())
        assert "`tmp_db`.`airi_invoice_fixture`" in sql
        return output([{"entity_id": "A", "invoice_amount_30d": "300"}])

    monkeypatch.setattr("airi.infrastructure.query_executor.run_cursor", cursor_output)
    _spark_worker(
        pipe,
        {
            "host": "unit.invalid",
            "port": 10000,
            "username": "unit",
            "database": "tmp_db",
            "mapping": True,
        },
        code,
        plan().model_dump_json(),
    )
    state, payload = pipe.send.call_args.args[0]
    assert state == ("failure" if unsupported else "success")
    if unsupported:
        assert payload["category"] == "unsupported_source_schema"
    else:
        assert (
            QueryOutput.model_validate_json(payload).source_mapping == "tmp_db.airi_invoice_fixture"
        )
    assert "configuration" not in factory.call_args.kwargs
    connection.close.assert_called_once()


def test_absent_environment_stays_unverified():
    settings = Settings(_env_file=None)
    report = SparkEnvironmentProbe(SparkSQLExecutor(settings), settings).validate()
    assert report.overall == "not_verified"
    assert report.connectivity == "not_verified"
    assert all(value == "not_verified" for value in report.capabilities.values())

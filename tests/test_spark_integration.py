"""Real Spark only. Explicit marker + server configuration; never Mock success."""

import pytest
from fastapi.testclient import TestClient
from test_phase2 import NAMES, app_for, approve_fixture, generate, submit

from airi.core.config import Settings
from airi.environments.service import SparkEnvironmentProbe
from airi.infrastructure.query_executor import SparkSQLExecutor

pytestmark = pytest.mark.spark_integration


@pytest.fixture
def spark_settings(settings):
    remote = Settings()
    if (
        remote.effective_execution_mode != "spark_test"
        or remote.environment != "test"
        or not (remote.spark_host or remote.spark_test_host)
        or not remote.spark_fixture_mapping
        or not remote.spark_read_only_attested
    ):
        pytest.skip(
            "NOT VERIFIED: requires explicit isolated Spark config, mapping and attestation"
        )
    return remote.model_copy(update={"database_url": settings.database_url})


@pytest.mark.parametrize("name", NAMES)
def test_approved_real_fixture(spark_settings, name):
    executor = SparkSQLExecutor(spark_settings)
    with TestClient(app_for(spark_settings, name, executor)) as client:
        draft = generate(client, name)
        approval = submit(client, draft)
        approve_fixture(
            client,
            approval,
            json={
                "reviewer": "integration-operator",
                "comment": "Operator provisioned test fixture",
            },
        )
        response = client.post(
            "/api/v1/tests/run",
            json={
                "artifact_id": draft["artifact"]["artifact_id"],
                "approval_id": approval,
                "max_rows": 1000,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution"]["status"] == "success", body["execution"]["failure_category"]
        assert body["execution"]["source_mapping"] == "tmp_db.airi_invoice_fixture"
        assert body["report"]["failed"] == 0
        assert body["report"]["results"][-1]["status"] == "passed"


def test_environment_acceptance(spark_settings):
    report = SparkEnvironmentProbe(SparkSQLExecutor(spark_settings), spark_settings).validate()
    assert report.connectivity == "passed"
    assert report.overall in ("passed", "passed_with_warnings")
    assert len(report.fixture_parity) == 3
    assert all(p.status == "passed" for p in report.fixture_parity)

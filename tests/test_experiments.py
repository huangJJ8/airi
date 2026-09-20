import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_phase2 import NAMES, app_for, approve_fixture, generate, submit

from airi.experiments.fixtures import experiment_fixture
from airi.experiments.models import DatasetSnapshot, LabelDefinition
from airi.experiments.validation import ExperimentError, dataset_checksum, evaluation_dataset
from airi.infrastructure.query_executor import MockQueryExecutor


def snapshot_payload(rows):
    return {
        "name": "sample_20260909",
        "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
        "snapshot_time": "2026-09-09T00:00:00+08:00",
        "partition": {"field": "dt", "value": "20260909"},
        "row_count": len(rows),
        "checksum": dataset_checksum(rows),
    }


def spec_payload(draft, approval, tests, dataset, label):
    return {
        "experiment_name": "fixture_evaluation",
        "metric": {"artifact_id": draft["artifact"]["artifact_id"]},
        "approval_id": approval,
        "test_run_id": tests["test_run_id"],
        "dataset_snapshot_id": dataset["dataset_snapshot_id"],
        "label_definition_id": label["label_definition_id"],
        "anchor_time": "2026-09-09T00:00:00+08:00",
        "observation_time": "2026-09-09T00:00:00+08:00",
        "label_window": {"start": "2026-09-10T00:00:00+08:00", "end": "2026-10-09T00:00:00+08:00"},
    }


def setup_experiment(client, name, rows):
    draft = generate(client, name)
    approval = submit(client, draft)
    approve_fixture(
        client, approval, json={"reviewer": "fixture", "comment": "Synthetic test only"}
    )
    tested = client.post(
        "/api/v1/tests/run",
        json={
            "artifact_id": draft["artifact"]["artifact_id"],
            "approval_id": approval,
            "max_rows": 1000,
        },
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["report"]["status"] in ("passed", "passed_with_warnings")
    dataset = client.post("/api/v1/datasets", json=snapshot_payload(rows))
    label = client.post("/api/v1/labels", json={"name": "binary_label"})
    assert dataset.status_code == label.status_code == 201
    return spec_payload(draft, approval, tested.json()["report"], dataset.json(), label.json())


@pytest.mark.parametrize("name", NAMES)
def test_full_experiment(settings, name):
    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    with TestClient(
        app_for(settings, name, MockQueryExecutor(source, dataset_rows=rows))
    ) as client:
        payload = setup_experiment(client, name, rows)
        spec = client.post("/api/v1/experiments", json=payload)
        assert spec.status_code == 201, spec.text
        run = client.post(f"/api/v1/experiments/{spec.json()['experiment_spec_id']}/run")
        assert run.status_code == 200, run.text
        result = run.json()
        assert result["run"]["status"] == "completed", result
        assert result["evaluation"]["total_sample"] == 200
        assert result["evaluation"]["labeled_sample"] == 188
        assert result["join_summary"]["label_only"] > 0
        assert result["execution_mode"] == "mock"
        assert result["requires_human_review"]
        assert (
            client.get(f"/api/v1/experiments/runs/{result['run']['experiment_run_id']}").json()
            == result
        )
        repeat = client.post(f"/api/v1/experiments/{spec.json()['experiment_spec_id']}/run").json()
        assert repeat["evaluation"] == result["evaluation"]


@pytest.mark.parametrize(
    "change", ["future", "test_failed", "spark_unverified", "spark_not_verified", "unapproved"]
)
def test_experiment_gates(settings, change):
    from airi.approvals.persistence import DevelopmentArtifactRow
    from airi.testing.persistence import MetricTestRunRow

    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    app = app_for(settings, NAMES[0], MockQueryExecutor(source, dataset_rows=rows))
    with TestClient(app) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        if change == "future":
            payload["label_window"]["start"] = payload["anchor_time"]
        elif change in ("spark_unverified", "spark_not_verified"):
            settings.execution_mode = "spark_test"
            if change == "spark_not_verified":
                from airi.approvals.service import utcnow
                from airi.environments.models import EnvironmentValidationReport
                from airi.environments.persistence import EnvironmentValidationRow

                report = EnvironmentValidationReport(
                    overall="not_verified", connectivity="not_verified"
                )
                payload["environment_validation_run_id"] = report.validation_run_id
                with app.state.database.session_factory() as session:
                    session.add(
                        EnvironmentValidationRow(
                            validation_run_id=report.validation_run_id,
                            environment="spark_test",
                            profile_version="1.0.0",
                            status="not_verified",
                            engine_version=None,
                            session_timezone=None,
                            started_at=utcnow(),
                            finished_at=utcnow(),
                            created_at=utcnow(),
                            report_json=report.model_dump(mode="json"),
                        )
                    )
                    session.commit()
        else:
            with app.state.database.session_factory() as session:
                if change == "test_failed":
                    record = session.get(MetricTestRunRow, payload["test_run_id"])
                    record.status = "failed"
                else:
                    record = session.get(DevelopmentArtifactRow, payload["metric"]["artifact_id"])
                    record.status = "rejected"
                session.commit()
        response = client.post("/api/v1/experiments", json=payload)
        assert response.status_code == 409, response.text


def test_dataset_duplicates_unknown_and_checksum():
    rows = [{"seller_tax_no": "A", "bad_flag": 1, "dt": "20260909"}]
    snapshot = DatasetSnapshot.model_validate(snapshot_payload(rows))
    label = LabelDefinition(name="label")
    data, counts = evaluation_dataset([], rows, "metric", snapshot, label)
    assert data == [(None, True)] and counts["label_only"] == 1
    duplicate = rows * 2
    with pytest.raises(ExperimentError, match="duplicate_entity"):
        evaluation_dataset(
            [],
            duplicate,
            "metric",
            DatasetSnapshot.model_validate(snapshot_payload(duplicate)),
            label,
        )
    with pytest.raises(ExperimentError, match="dataset_invalid"):
        evaluation_dataset([], [], "metric", snapshot, label)
    with pytest.raises(ExperimentError, match="duplicate_entity"):
        evaluation_dataset([{"entity_id": "A", "metric": 1}] * 2, rows, "metric", snapshot, label)
    rows[0]["bad_flag"] = 2
    with pytest.raises(ExperimentError, match="label_invalid"):
        evaluation_dataset(
            [], rows, "metric", DatasetSnapshot.model_validate(snapshot_payload(rows)), label
        )


def test_schema_validation():
    with pytest.raises(ValidationError):
        LabelDefinition(name="label", good_value=1)
    with pytest.raises(ValidationError):
        DatasetSnapshot.model_validate({"name": "missing_partition"})
    with pytest.raises(ValidationError):
        LabelDefinition(name="label", bad_value=True)


@pytest.mark.parametrize("mutation", ["dataset", "source", "failure", "truncated"])
def test_failed_runs_are_saved(settings, mutation):
    from airi.execution.models import QueryOutput

    settings.execution_mode = "mock"
    source, rows = experiment_fixture()
    executor = MockQueryExecutor(source, dataset_rows=rows)
    with TestClient(app_for(settings, NAMES[0], executor)) as client:
        payload = setup_experiment(client, NAMES[0], rows)
        spec = client.post("/api/v1/experiments", json=payload).json()
        url = f"/api/v1/experiments/{spec['experiment_spec_id']}/run"
        baseline = client.post(url).json()
        assert baseline["run"]["status"] == "completed"
        if mutation == "dataset":
            executor.dataset_rows = rows[:-1]
        elif mutation == "source":
            executor.fixture_rows[0]["invoice_amt"] = "999999"
        elif mutation == "failure":
            executor.failure = "timeout"
        else:
            executor.load_dataset = lambda snapshot, plan: QueryOutput(
                columns=[], rows=[], truncated=True
            )
        failed = client.post(url).json()
        assert failed["run"]["status"] == "failed", failed
        assert failed["evaluation"] is None
        assert (
            client.get(f"/api/v1/experiments/runs/{failed['run']['experiment_run_id']}").json()
            == failed
        )
        assert client.post(url, json={"sql": "SELECT 1"}).status_code == 422


def test_api_errors_and_swapped_labels(client):
    assert client.get("/api/v1/experiments/runs/missing").status_code == 404
    response = client.post("/api/v1/datasets", json={"sql": "SELECT 1"})
    assert response.status_code == 422 and response.headers["X-Request-ID"]
    rows = [
        {"seller_tax_no": "A", "bad_flag": 0, "dt": "20260909"},
        {"seller_tax_no": "B", "bad_flag": None, "dt": "20260909"},
    ]
    snapshot = DatasetSnapshot.model_validate(snapshot_payload(rows))
    label = LabelDefinition(name="reverse_label", bad_value=0, good_value=1)
    data, counts = evaluation_dataset([], rows, "metric", snapshot, label)
    assert data == [(None, True)] and counts["unknown_label_excluded"] == 1

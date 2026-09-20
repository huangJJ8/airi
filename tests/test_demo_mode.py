"""Phase 10 demo-mode plumbing: explicit, honest, and never a silent fallback."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from airi.core.config import Settings
from airi.infrastructure.demo_llm import DemoRequirementLLM
from airi.main import create_app


def test_demo_requirement_llm_parses_the_demo_requirement():
    raw = DemoRequirementLLM().complete_structured(
        system_prompt="unused",
        user_prompt='{"requirement": "统计企业近30天开票金额"}',
        schema={},
    )
    import json

    output = json.loads(raw)
    assert output["unsupported_reason"] is None
    assert output["metric_ir"]["name"] == "invoice_amount_30d"
    assert output["metric_ir"]["aggregation"] == {"function": "sum", "field": "invoice_amt"}
    assert output["metric_ir"]["window"]["size"] == 30


def test_demo_requirement_llm_supports_count_and_window_variants():
    import json

    llm = DemoRequirementLLM()
    output = json.loads(
        llm.complete_structured(
            system_prompt="unused",
            user_prompt='{"requirement": "统计企业近60天开票次数"}',
            schema={},
        )
    )
    assert output["metric_ir"]["name"] == "invoice_count_60d"
    assert output["metric_ir"]["aggregation"] == {"function": "count", "field": None}


def test_demo_requirement_llm_refuses_unknown_requirements():
    import json

    raw = DemoRequirementLLM().complete_structured(
        system_prompt="unused",
        user_prompt='{"requirement": "预测明天下不下雨"}',
        schema={},
    )
    output = json.loads(raw)
    assert output["metric_ir"] is None
    assert "invoice_risk" in output["unsupported_reason"]


def test_wildcard_cors_origin_is_rejected():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="test",
            database_url="sqlite+pysqlite:///.demo/cors.db",
            cors_origins=["*"],
        )


def test_demo_fixtures_load_the_seeded_refinement_rows():
    """The Web-guided experiment must see exactly the rows seed_demo.py registered."""
    config = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///.demo/demo_fixtures.db",
        execution_mode="mock",
        demo_fixtures=True,
    )
    app = create_app(config)
    executor = app.state.query_executor
    from airi.experiments.validation import dataset_checksum
    from airi.refinement.fixtures import refinement_fixture

    _, labels = refinement_fixture()
    assert len(executor.dataset_rows) == len(labels)
    assert dataset_checksum(executor.dataset_rows) == dataset_checksum(labels)


def test_meta_reports_runtime_facts(client):
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["app_name"] == "AIRI"
    assert body["environment"] == "test"
    assert body["execution_mode"] in {"disabled", "mock", "spark_test"}
    assert body["llm_mode"] == "openai_compatible"
    assert body["production_deployed"] is False


def test_demo_mock_mode_generates_a_metric_end_to_end(settings):
    config = Settings(
        _env_file=None,
        environment="test",
        database_url=settings.database_url,
        execution_mode="mock",
        llm_mode="demo_mock",
    )
    with TestClient(create_app(config)) as demo_client:
        response = demo_client.post(
            "/api/v1/development/generate",
            json={
                "requirement": "统计企业近30天开票金额",
                "scenario": "invoice_risk",
                "execution_context": {"anchor_time": "2026-09-09T00:00:00+08:00"},
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["metric_ir"]["name"] == "invoice_amount_30d"
        assert result["validation"]["valid"] is True
        assert result["requires_human_review"] is True

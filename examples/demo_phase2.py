"""Explicit Mock + SQLite demo. Never connects to Spark or a production database."""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.infrastructure.llm import MockLLMClient
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.main import create_app


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    Path(".demo").mkdir(exist_ok=True)
    Path("examples/phase2").mkdir(exist_ok=True)
    settings = Settings(
        _env_file=None, environment="test", database_url="sqlite+pysqlite:///.demo/phase2_demo.db"
    )
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    for name in ("invoice_amount_30d", "invoice_count_30d", "invoice_amount_growth_30d"):
        llm = MockLLMClient(
            json.dumps({"metric_ir": load(f"examples/{name}_ir.json"), "unsupported_reason": None})
        )
        executor = MockQueryExecutor(load("tests/fixtures/invoices.json"))
        with TestClient(create_app(settings, llm_client=llm, query_executor=executor)) as client:
            generated = client.post(
                "/api/v1/development/generate", json=load(f"examples/{name}_request.json")
            )
            generated.raise_for_status()
            draft = generated.json()
            review = client.post(
                "/api/v1/approvals",
                json={
                    "artifact_id": draft["artifact"]["artifact_id"],
                    "metric_ir_hash": canonical_hash(draft["metric_ir"]),
                    "artifact_hash": draft["artifact"]["content_hash"],
                },
            )
            review.raise_for_status()
            approval_id = review.json()["approval_id"]
            decision = client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                json={"reviewer": "mock-demo-reviewer", "comment": "Synthetic fixture only"},
            )
            decision.raise_for_status()
            request = {
                "artifact_id": draft["artifact"]["artifact_id"],
                "approval_id": approval_id,
                "execution_profile": "spark_test",
            }
            executed = client.post("/api/v1/executions", json=request)
            executed.raise_for_status()
            tested = client.post("/api/v1/tests/run", json=request)
            tested.raise_for_status()
            for suffix, value in (
                ("request", request),
                ("execution", executed.json()),
                ("testing", tested.json()),
            ):
                Path(f"examples/phase2/{name}_{suffix}.json").write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            report = tested.json()["report"]
            print(name, report["status"], report["passed"], report["warnings"])


if __name__ == "__main__":
    main()

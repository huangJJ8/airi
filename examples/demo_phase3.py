"""Synthetic, offline Phase 3 evidence; approval here is demo-only."""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.experiments.fixtures import experiment_fixture
from airi.experiments.validation import dataset_checksum
from airi.infrastructure.llm import MockLLMClient
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.main import create_app


def main(
    output_dir="examples/phase3",
    database_file="phase3_demo.db",
    shared=False,
    fixture_factory=experiment_fixture,
    names=("invoice_amount_30d", "invoice_count_30d", "invoice_amount_growth_30d"),
):
    Path(".demo").mkdir(exist_ok=True)
    output = Path(output_dir)
    output.mkdir(exist_ok=True, parents=True)
    settings = Settings(
        _env_file=None,
        environment="test",
        execution_mode="mock",
        database_url=f"sqlite+pysqlite:///.demo/{database_file}",
    )
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    dataset = label = None
    reports = []
    for name in names:
        ir = json.loads(Path(f"examples/{name}_ir.json").read_text(encoding="utf-8"))
        llm = MockLLMClient(json.dumps({"metric_ir": ir, "unsupported_reason": None}))
        source, rows = fixture_factory()
        executor = MockQueryExecutor(source, dataset_rows=rows)
        with TestClient(create_app(settings, llm_client=llm, query_executor=executor)) as client:

            def post(path, body=None):
                response = client.post("/api/v1/" + path, json=body)
                response.raise_for_status()
                return response.json()

            draft = post(
                "development/generate",
                json.loads(Path(f"examples/{name}_request.json").read_text(encoding="utf-8")),
            )
            artifact_id = draft["artifact"]["artifact_id"]
            approval = post(
                "approvals",
                {
                    "artifact_id": artifact_id,
                    "metric_ir_hash": canonical_hash(draft["metric_ir"]),
                    "artifact_hash": draft["artifact"]["content_hash"],
                },
            )["approval_id"]
            post(
                f"approvals/{approval}/approve",
                {"reviewer": "synthetic-demo", "comment": "Synthetic fixture only"},
            )
            tested = post(
                "tests/run", {"artifact_id": artifact_id, "approval_id": approval, "max_rows": 1000}
            )["report"]
            assert tested["status"] in ("passed", "passed_with_warnings")
            anchor = "2026-09-09T00:00:00+08:00"
            if not shared or dataset is None:
                dataset = post(
                    "datasets",
                    {
                        "name": "invoice_sample_20260909",
                        "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
                        "snapshot_time": anchor,
                        "partition": {"field": "dt", "value": "20260909"},
                        "row_count": len(rows),
                        "checksum": dataset_checksum(rows),
                    },
                )
                label = post("labels", {"name": "synthetic_binary_label"})
            spec = post(
                "experiments",
                {
                    "experiment_name": name + "_evaluation",
                    "metric": {"artifact_id": artifact_id},
                    "approval_id": approval,
                    "test_run_id": tested["test_run_id"],
                    "dataset_snapshot_id": dataset["dataset_snapshot_id"],
                    "label_definition_id": label["label_definition_id"],
                    "anchor_time": anchor,
                    "observation_time": anchor,
                    "label_window": {
                        "start": "2026-09-10T00:00:00+08:00",
                        "end": "2026-10-09T00:00:00+08:00",
                    },
                },
            )
            report = post(f"experiments/{spec['experiment_spec_id']}/run")
            assert report["run"]["status"] == "completed", report
            reports.append(report)
            for suffix, value in (("spec", spec), ("report", report)):
                (output / f"{name}_{suffix}.json").write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            e = report["evaluation"]
            print(name, e["coverage"], e["ks"]["value"], e["iv"], e["ks"]["direction"])
    return settings, reports


if __name__ == "__main__":
    main()

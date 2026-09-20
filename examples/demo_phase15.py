"""Local-only Mock LLM demo: migrate SQLite, generate three drafts and review each."""

import json
from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.infrastructure.llm import MockLLMClient
from airi.main import create_app


def main() -> None:
    directory = Path(__file__).resolve().parent
    root = directory.parent
    data_directory = root / ".demo"
    data_directory.mkdir(exist_ok=True)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{(data_directory / 'reviews.db').as_posix()}",
    )
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        migration = Config(str(root / "alembic.ini"))
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    engine.dispose()
    amount = json.loads((directory / "invoice_metric_ir.json").read_text(encoding="utf-8"))
    count = amount | {
        "name": "invoice_count_30d",
        "display_name": "近30天企业开票次数",
        "description": "企业近30日开票原始记录行数",
        "aggregation": {"function": "count", "field": None},
    }
    growth = {
        "schema_version": "1.0.0",
        "metric_type": "derived",
        "name": "invoice_amount_growth_30d",
        "display_name": "近30天企业开票金额增长率",
        "description": "本30日开票金额相对前30日的增长率",
        "dependencies": [
            {"role": "current", "metric": deepcopy(amount), "anchor_offset_days": 0},
            {"role": "previous", "metric": deepcopy(amount), "anchor_offset_days": 30},
        ],
        "expression": {"operator": "growth_rate"},
        "zero_division": {"strategy": "null"},
    }
    for metric in (amount, count, growth):
        llm = MockLLMClient(json.dumps({"metric_ir": metric, "unsupported_reason": None}))
        request = {
            "requirement": metric["display_name"],
            "scenario": "invoice_risk",
            "execution_context": {"anchor_time": "2026-09-09T00:00:00+08:00"},
        }
        with TestClient(create_app(settings, llm_client=llm)) as client:
            response = client.post("/api/v1/development/generate", json=request)
            response.raise_for_status()
            result = response.json()
            submit_request = {
                "artifact_id": result["artifact"]["artifact_id"],
                "artifact_hash": result["artifact"]["content_hash"],
                "metric_ir_hash": canonical_hash(result["metric_ir"]),
            }
            submit = client.post("/api/v1/approvals", json=submit_request)
            submit.raise_for_status()
            approval = client.post(
                f"/api/v1/approvals/{submit.json()['approval_id']}/approve",
                json={"reviewer": "iris-demo", "comment": "本地 Mock 演示审批，非生产授权"},
            )
            approval.raise_for_status()
        stem = metric["name"]
        artifacts = {
            "ir": metric,
            "request": request,
            "result": result,
            "review": {
                "submit_request": submit_request,
                "pending": submit.json(),
                "decision": approval.json(),
            },
        }
        for suffix, content in artifacts.items():
            (directory / f"{stem}_{suffix}.json").write_text(
                json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (directory / f"{stem}.sql").write_text(result["artifact"]["code"], encoding="utf-8")
        print(f"{stem}: validated draft -> pending_review -> approved (local demo)")


if __name__ == "__main__":
    main()

"""Synthetic Phase 11 Multi-Scenario Validation Demo.

The name is the first honest sentence in this file: everything below runs on
the synthetic ``demo`` relationship tables, mock execution and an explicit
``demo_mock`` LLM double, so it can never verify a real runtime. What it
demonstrates is the one claim Phase 11 exists for:

    New Scenario != New Workflow

The same AIRI process is driven twice with two deliberately different
requirements:

    Scenario A  统计企业近30天开票金额
                -> invoice_risk
                -> metric_sum + metric_window
                -> single-table windowed SUM

    Scenario B  统计企业通过关联自然人间接关联的其他企业数量
                -> enterprise_relation
                -> metric_join + metric_count
                -> two-hop JOIN + COUNT DISTINCT with a self-exclusion

Nothing scenario-specific is added to the workflow: the join mechanism lives
in the shared Join IR / join SQL tool / join grammar validator, and the
business path lives only in the ``enterprise_relation`` scenario skill. The
demo finishes by proving the isolation side: an invoice requirement asked of
the relation scenario (and the reverse, and a vague requirement) is refused
with ``requirement_parse_failed`` instead of guessed.
"""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.experiments.validation import dataset_checksum
from airi.infrastructure.relation_fixture import relation_fixture
from airi.main import create_app

OUTPUT = Path("examples/phase11")
DEMO_DB = Path(".demo/phase11_demo.db")
ANCHOR = "2026-09-09T00:00:00+08:00"
REVIEWER = "synthetic-demo"

INVOICE_REQUIREMENT = "统计企业近30天开票金额"
RELATION_REQUIREMENT = "统计企业通过关联自然人间接关联的其他企业数量"
AMBIGUOUS_REQUIREMENTS = [
    # vague: no declared two-hop path -> must be refused, never guessed
    ("统计企业关联数量", "enterprise_relation"),
    # cross-family: an invoice window asked of the relation scenario
    (INVOICE_REQUIREMENT, "enterprise_relation"),
    # cross-family: a relationship count asked of the invoice scenario
    (RELATION_REQUIREMENT, "invoice_risk"),
]


def build_app() -> Settings:
    DEMO_DB.parent.mkdir(exist_ok=True)
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    settings = Settings(
        _env_file=None,
        environment="local",
        execution_mode="mock",
        llm_mode="demo_mock",
        demo_fixtures=True,
        database_url=f"sqlite+pysqlite:///{DEMO_DB.as_posix()}",
    )
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    return settings


def save(name: str, value) -> Path:
    path = OUTPUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    settings = build_app()
    relation = relation_fixture()

    with TestClient(create_app(settings)) as client:

        def post(path, body=None):
            response = client.post("/api/v1/" + path, json=body)
            response.raise_for_status()
            return response.json()

        def generate(requirement, scenario):
            response = client.post(
                "/api/v1/development/generate",
                json={
                    "requirement": requirement,
                    "scenario": scenario,
                    "execution_context": {"anchor_time": ANCHOR},
                },
            )
            return response

        def approve(draft):
            approval = post(
                "approvals",
                {
                    "artifact_id": draft["artifact"]["artifact_id"],
                    "metric_ir_hash": canonical_hash(draft["metric_ir"]),
                    "artifact_hash": draft["artifact"]["content_hash"],
                },
            )
            post(
                f"approvals/{approval['approval_id']}/approve",
                {"reviewer": REVIEWER, "comment": "Fixture SQL review only"},
            )
            return approval["approval_id"]

        print("=" * 78)
        print("Synthetic Phase 11 Multi-Scenario Validation Demo")
        print("Not a real runtime verification: synthetic demo tables + mock execution.")
        print("=" * 78)

        # ============================================ Scenario A: invoice_risk
        print("\n--- Scenario A  统计企业近30天开票金额 ----------")
        invoice = generate(INVOICE_REQUIREMENT, "invoice_risk").json()
        invoice_capabilities = [
            f"{skill['name']}@{skill['version']}" for skill in invoice["skill_plan"]["capabilities"]
        ]
        print("scenario skill :", invoice["skill_plan"]["scenario_skill"]["name"])
        print("capabilities   :", ", ".join(invoice_capabilities))
        print("sql tool       :", invoice["tool_plan"]["tools"][0]["name"])
        print("validation     :", "valid" if invoice["validation"]["valid"] else "INVALID")
        save("invoice_skill_plan.json", invoice["skill_plan"])
        save("invoice_metric_ir.json", invoice["metric_ir"])

        # ============================================ Scenario B: enterprise_relation
        print("\n--- Scenario B  统计企业通过关联自然人间接关联的其他企业数量 ---")
        relation_draft = generate(RELATION_REQUIREMENT, "enterprise_relation").json()
        relation_capabilities = [
            f"{skill['name']}@{skill['version']}"
            for skill in relation_draft["skill_plan"]["capabilities"]
        ]
        ir = relation_draft["metric_ir"]
        join = ir["joins"][0]
        print("scenario skill :", relation_draft["skill_plan"]["scenario_skill"]["name"])
        print("capabilities   :", ", ".join(relation_capabilities))
        print("metric         :", ir["name"], "= COUNT DISTINCT", ir["aggregation"]["field"])
        print("path           :", ir["source"]["table"], "->", join["source"]["table"])
        print(
            "join key       :",
            f"{join['conditions'][0]['left']['alias']}."
            f"{join['conditions'][0]['left']['field']} = "
            f"{join['conditions'][0]['right']['alias']}."
            f"{join['conditions'][0]['right']['field']}",
        )
        print(
            "self-exclusion :",
            ir["column_filters"][0]["left"]["field"],
            ir["column_filters"][0]["operator"],
            f"{ir['column_filters'][0]['right']['alias']}."
            f"{ir['column_filters'][0]['right']['field']}",
        )
        print("validation     :", "valid" if relation_draft["validation"]["valid"] else "INVALID")
        save("relation_skill_plan.json", relation_draft["skill_plan"])
        save("relation_metric_ir.json", ir)

        relation_artifact_id = relation_draft["artifact"]["artifact_id"]
        stored = client.get(f"/api/v1/development/artifacts/{relation_artifact_id}").json()
        save("relation_join_sql.sql", stored["code"] + "\n")
        print("join SQL saved : examples/phase11/relation_join_sql.sql")

        # ------------------------------------------ governed testing (8 checks)
        approval_id = approve(relation_draft)
        tested = post(
            "tests/run",
            {
                "artifact_id": relation_artifact_id,
                "approval_id": approval_id,
                "max_rows": 1000,
            },
        )["report"]
        assert tested["status"] == "passed", tested
        assert tested["total"] == 8, tested["total"]
        print("\ntesting        :", tested["status"], f"{tested['passed']}/{tested['total']}")
        for case in tested["results"]:
            print(f"  {case['type']:<28} {case['status']:<8} {case['actual_summary']}")
        save("relation_test_report.json", tested)

        # ------------------------------------------ experiment on synthetic labels
        dataset = post(
            "datasets",
            {
                "name": "enterprise_relation_sample_20260911",
                "source": {"database": "tmp_db", "table": "enterprise_relation_sample"},
                "entity_key": "enterprise_id",
                "snapshot_time": ANCHOR,
                "partition": {"field": "dt", "value": "20260911"},
                "row_count": len(relation.labeled_rows),
                "checksum": dataset_checksum(relation.labeled_rows, "enterprise_id"),
            },
        )
        label = post("labels", {"name": "synthetic_relation_label", "entity_key": "enterprise_id"})
        spec = post(
            "experiments",
            {
                "experiment_name": "related_enterprise_count_evaluation",
                "metric": {"artifact_id": relation_artifact_id},
                "approval_id": approval_id,
                "test_run_id": tested["test_run_id"],
                "dataset_snapshot_id": dataset["dataset_snapshot_id"],
                "label_definition_id": label["label_definition_id"],
                "anchor_time": ANCHOR,
                "observation_time": ANCHOR,
                "label_window": {
                    "start": "2026-09-10T00:00:00+08:00",
                    "end": "2026-10-09T00:00:00+08:00",
                },
            },
        )
        run = post(f"experiments/{spec['experiment_spec_id']}/run")
        assert run["run"]["status"] == "completed", run
        evaluation = run["evaluation"]
        print("\nexperiment     :", evaluation["metric_name"])
        print("coverage       :", evaluation["coverage"])
        print("ks             :", evaluation["ks"]["value"], evaluation["ks"]["direction"])
        print("iv             :", evaluation["iv"])
        save("relation_evaluation.json", evaluation)

        # ------------------------------------------ scenario isolation
        print("\n--- Scenario isolation: vague / cross-family is refused, never guessed")
        rejections = []
        for requirement, scenario in AMBIGUOUS_REQUIREMENTS:
            response = generate(requirement, scenario)
            assert response.status_code == 422, response.text
            error = response.json()["error"]
            assert error["code"] == "requirement_parse_failed", error
            print(f"  refused [{scenario}] {requirement!r} -> {error['code']}")
            rejections.append({"requirement": requirement, "scenario": scenario, "error": error})
        save("isolation_rejections.json", rejections)

        # ------------------------------------------ the generalization claim
        print("\n--- Same workflow, different knowledge --------------------")
        print(f"{'':16}{'Scenario A':<28}Scenario B")
        print(f"{'requirement':<16}{INVOICE_REQUIREMENT:<24}{RELATION_REQUIREMENT}")
        print(f"{'scenario skill':<16}{'invoice_risk':<28}enterprise_relation")
        print(f"{'capabilities':<16}{'metric_window + metric_sum':<40}metric_join + metric_count")
        print(f"{'sql shape':<16}{'single-table windowed SUM':<28}two-hop JOIN + COUNT DISTINCT")
        print("\nArtifacts written to", OUTPUT)
        print("Reminder: no real LLM, no real Spark and no real enterprise data were used.")


if __name__ == "__main__":
    main()

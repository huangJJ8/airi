"""AIRI quick demo — the whole chain in one terminal command.

    uv run --frozen python examples/quick_demo.py

Drives one requirement end to end:

    requirement -> scenario -> Metric IR -> skills -> tool -> SQL
                -> approval -> testing -> experiment -> reflection

Everything runs locally and offline: SQLite, mock execution, synthetic fixture
data, and the explicit deterministic ``demo_mock`` LLM substitute. It is a tour
of the *pipeline*, not a verification of any real runtime, real model, or real
financial performance.

Fast on purpose: it does not replay the phase-by-phase history. For the
multi-scenario comparison, see ``examples/demo_phase11.py``.
"""

import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airi.approvals.artifacts import canonical_hash  # noqa: E402
from airi.core.config import Settings  # noqa: E402
from airi.experiments.validation import dataset_checksum  # noqa: E402
from airi.infrastructure.demo_llm import DemoRequirementLLM  # noqa: E402
from airi.infrastructure.query_executor import MockQueryExecutor  # noqa: E402
from airi.infrastructure.relation_fixture import relation_fixture  # noqa: E402
from airi.main import create_app  # noqa: E402
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture  # noqa: E402

OUTPUT = Path(".demo/quick_demo")
DEMO_DB = Path(".demo/quick_demo.db")
ANCHOR = "2026-09-09T00:00:00+08:00"
REVIEWER = "synthetic-demo-reviewer"

REQUIREMENT = "统计企业近30天开票金额"
SECOND_REQUIREMENT = "统计企业关联自然人控制的其他企业数量"


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def prepare(settings: Settings) -> None:
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()


def save(name: str, value) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / name
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    settings = Settings(
        _env_file=None,
        environment="local",
        log_level="WARNING",  # keep the terminal tour readable
        execution_mode="mock",
        llm_mode="demo_mock",
        demo_fixtures=True,
        database_url=f"sqlite+pysqlite:///{DEMO_DB.as_posix()}",
    )
    prepare(settings)

    source, labels = refinement_fixture()
    relation = relation_fixture()
    app = create_app(
        settings,
        llm_client=DemoRequirementLLM(),
        reflection_llm=MockWindowReflectionLLM(),
        query_executor=MockQueryExecutor(
            source,
            dataset_rows=[*labels, *relation.labeled_rows],
            relation_tables=relation.tables,
        ),
    )

    print("=" * 76)
    print("AIRI quick demo — synthetic data, mock execution, no real LLM")
    print("Demonstrates the pipeline. NOT production verified.")
    print("=" * 76)

    with TestClient(app) as client:

        def post(path, body=None):
            response = client.post("/api/v1/" + path, json=body)
            response.raise_for_status()
            return response.json()

        def generate(requirement, scenario):
            return client.post(
                "/api/v1/development/generate",
                json={
                    "requirement": requirement,
                    "scenario": scenario,
                    "execution_context": {"anchor_time": ANCHOR},
                },
            )

        # ------------------------------------------------------------ 1. parse
        rule("1. Requirement")
        print(f"  {REQUIREMENT}")

        rule("2. Scenario + skills + tool")
        draft = generate(REQUIREMENT, "invoice_risk").json()
        plan = draft["skill_plan"]
        scenario_skill = plan["scenario_skill"]
        print(f"  scenario skill : {scenario_skill['name']}@{scenario_skill['version']}")
        for skill in plan["capabilities"]:
            print(f"  capability     : {skill['name']}@{skill['version']}")
        for tool in draft["tool_plan"]["tools"]:
            print(f"  tool           : {tool['name']}@{tool['version']}")
        save("skill_plan.json", plan)

        # ------------------------------------------------------------ 3. IR
        rule("3. Metric IR (the contract)")
        ir = draft["metric_ir"]
        window = ir["window"]
        print(f"  metric         : {ir['name']}")
        print(f"  entity         : {ir['entity_key']}")
        print(f"  aggregation    : {ir['aggregation']['function']}({ir['aggregation']['field']})")
        print(
            f"  window         : {window['size']} {window['unit']}"
            f" on {window['time_field']} ({window['timezone']})"
        )
        print(f"  joins          : {len(ir['joins'])} (single-source metric)")
        save("metric_ir.json", ir)

        # ------------------------------------------------------------ 4. SQL
        rule("4. Deterministic SQL (generated from the IR, not by the model)")
        artifact_id = draft["artifact"]["artifact_id"]
        stored = client.get(f"/api/v1/development/artifacts/{artifact_id}").json()
        sql = stored["code"].strip()
        for line in sql.splitlines():
            print(f"  {line}")
        print(f"\n  static validation : {'valid' if draft['validation']['valid'] else 'INVALID'}")
        print(f"  content hash      : {draft['artifact']['content_hash'][:16]}...")
        save("generated.sql", sql + "\n")

        # ------------------------------------------------------------ 5. approve
        rule("5. Human approval (binds the content hashes, not just the id)")
        approval = post(
            "approvals",
            {
                "artifact_id": artifact_id,
                "metric_ir_hash": canonical_hash(ir),
                "artifact_hash": draft["artifact"]["content_hash"],
            },
        )
        post(
            f"approvals/{approval['approval_id']}/approve",
            {"reviewer": REVIEWER, "comment": "Synthetic fixture review"},
        )
        approval_id = approval["approval_id"]
        print(f"  approved by {REVIEWER}")
        print("  re-approving different content would fail the hash check")

        # ------------------------------------------------------------ 6. tests
        rule("6. Automated testing")
        report = post(
            "tests/run",
            {"artifact_id": artifact_id, "approval_id": approval_id, "max_rows": 1000},
        )["report"]
        print(f"  status : {report['status']}  ({report['passed']}/{report['total']} passed)")
        for case in report["results"]:
            print(f"    {case['type']:<26} {case['status']}")
        save("test_report.json", report)

        # ------------------------------------------------------------ 7. experiment
        rule("7. Experiment (statistics computed by Python)")
        dataset = post(
            "datasets",
            {
                "name": "invoice_sample_20260909",
                "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
                "snapshot_time": ANCHOR,
                "partition": {"field": "dt", "value": "20260909"},
                "row_count": len(labels),
                "checksum": dataset_checksum(labels),
            },
        )
        label = post("labels", {"name": "synthetic_binary_label"})
        spec = post(
            "experiments",
            {
                "experiment_name": "invoice_amount_30d_evaluation",
                "metric": {"artifact_id": artifact_id},
                "approval_id": approval_id,
                "test_run_id": report["test_run_id"],
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
        print(f"  metric   : {evaluation['metric_name']}")
        print(f"  sample   : {evaluation['labeled_sample']} labeled rows")
        print(f"  coverage : {evaluation['coverage']:.4f}")
        print(f"  bad rate : {evaluation['bad_rate']:.4f}")
        print(f"  ks       : {evaluation['ks']['value']:.4f}  ({evaluation['ks']['direction']})")
        print(f"  iv       : {evaluation['iv']:.4f}  ({evaluation['iv_status']})")
        print(f"  bins     : {evaluation['actual_bins']}")
        print(f"  threshold candidates : {len(evaluation['threshold_candidates'])}")
        save("evaluation.json", evaluation)

        # ------------------------------------------------------------ 8. reflection
        rule("8. Reflection (hypotheses, stored separately from the facts)")
        reflection = post("reflections", {"experiment_run_ids": [run["run"]["experiment_run_id"]]})
        print(f"  output kind : {reflection['llm_output_kind']}")
        print(f"  status      : {reflection['llm_reflection_status']}")
        for finding in reflection["diagnostics"]:
            print(f"  diagnostic  : [{finding['severity']}] {finding['finding_type']}")
        narrative = reflection["llm_summary"] or reflection["summary"]
        for line in str(narrative).splitlines()[:6]:
            print(f"  | {line.strip()}")
        proposals = reflection["refinement_proposals"]
        print(f"  proposals   : {len(proposals)} (advisory; require an explicit human decision)")
        for proposal in proposals:
            print(f"    {proposal['proposal_type']} -> {proposal.get('target')}")
        print(f"  requires human review : {reflection['requires_human_review']}")
        save("reflection.json", reflection)

        # ------------------------------------------------------------ contrast
        rule("The same pipeline, a different domain")
        second = generate(SECOND_REQUIREMENT, "enterprise_relation").json()
        second_plan = second["skill_plan"]
        capabilities = ", ".join(s["name"] for s in second_plan["capabilities"])
        print(f"  {SECOND_REQUIREMENT}")
        print(f"  -> {second_plan['scenario_skill']['name']} x {capabilities}")
        print(f"  -> {second['metric_ir']['aggregation']['function']} over a two-hop join")
        print("\n  Same workflow. Different knowledge. No new pipeline.")

        rule("Outputs")
        for path in sorted(OUTPUT.iterdir()):
            print(f"  {path}")

    print(
        "\nReminder: synthetic fixture data, mock execution, deterministic demo_mock LLM.\n"
        "No real model, no real cluster, no real enterprise data. NOT production verified."
    )


if __name__ == "__main__":
    main()

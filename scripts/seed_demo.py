"""Seed the AIRI Web demo database through the real governed API chain.

This script replays the exact Phase 3 -> Phase 6 story the Web UI narrates,
against a local SQLite database, using the same synthetic fixtures the
examples use:

    requirement -> Metric IR -> SQL -> human approval -> testing
    -> experiment -> reflection -> proposal decision -> refinement (60d / 90d)
    -> temporal validation -> promotion review -> metric versions
    -> staging release -> validation -> release review -> activation
    -> rollback of the 90d candidate

Every human decision below is an explicitly simulated demo action. The result
is a registry that shows v1.0.0 (60d) ACTIVE and v2.0.0 (90d) RETIRED, with a
full release-event timeline.

Usage:
    uv run --frozen python scripts/seed_demo.py
"""

from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.experiments.validation import dataset_checksum
from airi.infrastructure.demo_llm import DemoRequirementLLM
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.infrastructure.relation_fixture import relation_fixture
from airi.main import create_app
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture
from airi.temporal.fixtures import temporal_fixture

DEMO_DB = Path(".demo/airi_web_demo.db")
REVIEWER = "demo-governance"
REQUIREMENT = "统计企业近30天开票金额"
RELATION_REQUIREMENT = "统计企业通过关联自然人间接关联的其他企业数量"
ANCHOR = "2026-09-09T00:00:00+08:00"
EXPECTED = {60: "1.0.0", 90: "2.0.0"}


def prepare_database() -> Settings:
    DEMO_DB.parent.mkdir(exist_ok=True)
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    settings = Settings(
        _env_file=None,
        environment="local",
        execution_mode="mock",
        database_url=f"sqlite+pysqlite:///{DEMO_DB.as_posix()}",
    )
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()
    return settings


def main() -> None:
    settings = prepare_database()
    source, labels = refinement_fixture()
    relation = relation_fixture()
    # One executor serves both demo families: the two synthetic datasets use
    # different partitions, so the snapshot's dt filter separates them.
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
    with TestClient(app) as client:

        def post(path, body=None):
            response = client.post("/api/v1/" + path, json=body)
            response.raise_for_status()
            return response.json()

        def get(path):
            response = client.get("/api/v1/" + path)
            response.raise_for_status()
            return response.json()

        def approve(artifact_id, ir_hash, artifact_hash):
            approval = post(
                "approvals",
                {
                    "artifact_id": artifact_id,
                    "metric_ir_hash": ir_hash,
                    "artifact_hash": artifact_hash,
                },
            )
            post(
                f"approvals/{approval['approval_id']}/approve",
                {"reviewer": REVIEWER, "comment": "Simulated demo SQL review"},
            )
            return approval["approval_id"]

        def approve_artifact(artifact):
            return approve(
                artifact["artifact_id"], artifact["metric_ir_hash"], artifact["content_hash"]
            )

        # --------------------------------------------------- baseline 30d metric
        draft = post(
            "development/generate",
            {
                "requirement": REQUIREMENT,
                "scenario": "invoice_risk",
                "execution_context": {"anchor_time": ANCHOR},
            },
        )
        approval_id = approve(
            draft["artifact"]["artifact_id"],
            canonical_hash(draft["metric_ir"]),
            draft["artifact"]["content_hash"],
        )
        tested = post(
            "tests/run",
            {
                "artifact_id": draft["artifact"]["artifact_id"],
                "approval_id": approval_id,
                "max_rows": 1000,
            },
        )["report"]
        assert tested["status"] in ("passed", "passed_with_warnings"), tested
        print("baseline", draft["metric_ir"]["name"], "tests:", tested["status"])

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
                "metric": {"artifact_id": draft["artifact"]["artifact_id"]},
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
        experiment = post(f"experiments/{spec['experiment_spec_id']}/run")
        assert experiment["run"]["status"] == "completed", experiment
        evaluation = experiment["evaluation"]
        print(
            "experiment",
            evaluation["metric_name"],
            "coverage",
            evaluation["coverage"],
            "ks",
            evaluation["ks"]["value"],
            "iv",
            evaluation["iv"],
        )

        # ------------------------------------------- reflection and proposal gate
        reflection = post(
            "reflections", {"experiment_run_ids": [experiment["run"]["experiment_run_id"]]}
        )
        rid = reflection["run"]["reflection_run_id"]
        proposal = client.get(f"/api/v1/reflections/{rid}/proposals").json()[0]
        post(
            f"reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "accepted_for_investigation",
                "reviewer": REVIEWER,
                "comment": "Fixture research, not production",
            },
        )

        # ------------------------------------------------------- temporal series
        invoices, rows, periods = temporal_fixture(mode="stable")
        app.state.query_executor.fixture_rows.extend(invoices)
        app.state.query_executor.dataset_rows.extend(rows)
        slices = []
        for index, (anchor, dataset_rows) in enumerate(periods):
            snapshot = post(
                "datasets",
                {
                    "name": f"temporal_{index}",
                    "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
                    "snapshot_time": anchor.isoformat(),
                    "partition": {"field": "dt", "value": anchor.strftime("%Y%m%d")},
                    "row_count": len(dataset_rows),
                    "checksum": dataset_checksum(dataset_rows),
                },
            )
            slices.append(
                {
                    "time_slice_id": f"slice-{index}",
                    "name": anchor.date().isoformat(),
                    "dataset_snapshot_id": snapshot["dataset_snapshot_id"],
                    "observation_time": anchor.isoformat(),
                    "label_window": {
                        "start": (anchor + timedelta(days=1)).isoformat(),
                        "end": (anchor + timedelta(days=30)).isoformat(),
                    },
                    "role": "oot" if index == 3 else "historical",
                }
            )
        series = post(
            "temporal-series",
            {
                "name": "web demo synthetic series",
                "slices": slices,
                "label_definition_id": experiment["run"]["label_definition_id"],
                "reference_slice_id": "slice-0",
            },
        )
        assert series["series_id"], series

        # -------------------------------------------- promotion -> metric version
        versions = {}
        for days in (60, 90):
            refinement = post(
                "refinements",
                {
                    "reflection_run_id": rid,
                    "proposal_id": proposal["proposal_id"],
                    "parameter_selection": {"window_days": days},
                },
            )
            approve_artifact(refinement["candidate"])
            refinement_id = refinement["run"]["refinement_run_id"]
            assert post(f"refinements/{refinement_id}/evaluate")["run"]["status"] == "completed"
            post(
                f"refinements/{refinement_id}/decision",
                {
                    "decision": "accept_candidate_for_further_validation",
                    "reviewer": REVIEWER,
                    "comment": "Temporal evidence requested; not a promotion",
                },
            )
            pending = post(
                "temporal-validations",
                {"source_refinement_run_id": refinement_id, "series_id": series["series_id"]},
            )
            run_id = pending["spec"]["temporal_validation_run_id"]
            for slice_artifact in pending["spec"]["slice_artifacts"]:
                approve(
                    slice_artifact["artifact_id"],
                    slice_artifact["metric_ir_hash"],
                    slice_artifact["content_hash"],
                )
            report = post(f"temporal-validations/{run_id}/run")
            assert report["promotion_eligibility"] == "eligible_for_review", report["diagnostics"]
            review = post("promotion-reviews", {"temporal_validation_run_id": run_id})
            decided = post(
                f"promotion-reviews/{review['promotion_review_id']}/decision",
                {
                    "decision": "approved_for_versioning",
                    "reviewer": REVIEWER,
                    "comment": "Synthetic governance demonstration; no production use",
                },
            )
            version = post(
                "metric-versions", {"promotion_review_id": decided["promotion_review_id"]}
            )
            assert version["version"] == EXPECTED[days], version["version"]
            versions[days] = version
            print("version", version["version"], version["metric_name"], version["status"])

        # ------------------------------------------------ governed release, twice
        active_version = None
        for days in (60, 90):
            version = versions[days]
            release = post(
                "releases",
                {
                    "metric_version_id": version["metric_version_id"],
                    "target_environment": "staging",
                },
            )
            release_id = release["release_id"]
            report = post(f"releases/{release_id}/validate")
            assert report["release_eligibility"] == "eligible_for_release_review", report[
                "findings"
            ]
            review = post(f"releases/{release_id}/review")
            post(
                f"release-reviews/{review['release_review_id']}/decision",
                {"decision": "approved", "reviewer": REVIEWER, "comment": "Simulated release"},
            )
            activated = post(
                f"releases/{release_id}/activate",
                {"expected_active_version": active_version},
            )
            active_version = activated["version"]
            print("release", activated["version"], activated["status"])

        # --------------------------------------------------------------- rollback
        target = versions[60]["version"]
        rollback = post(
            "metrics/invoice_amount/rollback",
            {
                "target_version": target,
                "reason": "Synthetic regression observed in the 90d version",
            },
        )
        post(
            f"rollback-reviews/{rollback['rollback_review_id']}/decision",
            {"decision": "approved", "reviewer": REVIEWER, "comment": "Simulated rollback"},
        )
        listed = get("metrics/invoice_amount/versions")
        assert [item["status"] for item in listed] == ["active", "retired"], listed
        events = get("metrics/invoice_amount/events")

        # -------------------------------------- Phase 11: enterprise_relation chain
        # The same governed chain, driven by a second scenario: generation picks
        # the metric_join capability, testing runs the eight relation checks,
        # and the experiment joins the metric rows with the relation label.
        relation_draft = post(
            "development/generate",
            {
                "requirement": RELATION_REQUIREMENT,
                "scenario": "enterprise_relation",
                "execution_context": {"anchor_time": ANCHOR},
            },
        )
        relation_approval_id = approve(
            relation_draft["artifact"]["artifact_id"],
            canonical_hash(relation_draft["metric_ir"]),
            relation_draft["artifact"]["content_hash"],
        )
        relation_tested = post(
            "tests/run",
            {
                "artifact_id": relation_draft["artifact"]["artifact_id"],
                "approval_id": relation_approval_id,
                "max_rows": 1000,
            },
        )["report"]
        assert relation_tested["status"] == "passed", relation_tested
        assert relation_tested["total"] == 8, relation_tested["total"]
        print(
            "relation",
            relation_draft["metric_ir"]["name"],
            "tests:",
            relation_tested["status"],
            f"{relation_tested['passed']}/{relation_tested['total']}",
        )

        relation_dataset = post(
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
        relation_label = post(
            "labels", {"name": "synthetic_relation_label", "entity_key": "enterprise_id"}
        )
        relation_spec = post(
            "experiments",
            {
                "experiment_name": "related_enterprise_count_evaluation",
                "metric": {"artifact_id": relation_draft["artifact"]["artifact_id"]},
                "approval_id": relation_approval_id,
                "test_run_id": relation_tested["test_run_id"],
                "dataset_snapshot_id": relation_dataset["dataset_snapshot_id"],
                "label_definition_id": relation_label["label_definition_id"],
                "anchor_time": ANCHOR,
                "observation_time": ANCHOR,
                "label_window": {
                    "start": "2026-09-10T00:00:00+08:00",
                    "end": "2026-10-09T00:00:00+08:00",
                },
            },
        )
        relation_experiment = post(f"experiments/{relation_spec['experiment_spec_id']}/run")
        assert relation_experiment["run"]["status"] == "completed", relation_experiment
        relation_evaluation = relation_experiment["evaluation"]
        print(
            "relation experiment",
            relation_evaluation["metric_name"],
            "coverage",
            relation_evaluation["coverage"],
            "ks",
            relation_evaluation["ks"]["value"],
            "direction",
            relation_evaluation["ks"]["direction"],
        )

        print("seed complete:", DEMO_DB)
        print("versions:", [(item["version"], item["status"]) for item in listed])
        print("release events:", len(events))


if __name__ == "__main__":
    main()

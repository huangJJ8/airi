"""Offline fixtures only. Every human decision below is an explicitly simulated demo action.

The demonstration never talks to Spark, MySQL or a real LLM. It answers one question:
can a promotion-approved candidate become an immutable version, be released through
independent gates, be activated logically, and be rolled back without losing history?
"""

import json
from datetime import timedelta
from pathlib import Path

from demo_phase3 import main as baseline_demo
from fastapi.testclient import TestClient

from airi.experiments.validation import dataset_checksum
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.main import create_app
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture
from airi.temporal.fixtures import temporal_fixture

OUTPUT = Path("examples/phase6")
DEMO_DB = Path(".demo/phase6_demo.db")
REVIEWER = "synthetic-demo"
# Server-owned version numbers derived from the family lineage, never from the client.
EXPECTED = {60: "1.0.0", 90: "2.0.0"}


def main():
    # A version registry is cumulative by design, so the demo starts from an empty demo
    # database; otherwise a second run would continue at 3.0.0 instead of 1.0.0.
    DEMO_DB.unlink(missing_ok=True)
    settings, experiments = baseline_demo(
        output_dir=str(OUTPUT / "baseline"),
        database_file="phase6_demo.db",
        fixture_factory=refinement_fixture,
        names=("invoice_amount_30d",),
    )
    source, labels = refinement_fixture()
    app = create_app(
        settings,
        reflection_llm=MockWindowReflectionLLM(),
        query_executor=MockQueryExecutor(source, dataset_rows=labels),
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

        def save(name, value):
            path = OUTPUT / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        def approve(artifact):
            """The SQL approval gate. Distinct from promotion and release review."""
            approval = post(
                "approvals",
                {
                    "artifact_id": artifact["artifact_id"],
                    "metric_ir_hash": artifact["metric_ir_hash"],
                    "artifact_hash": artifact["content_hash"],
                },
            )
            post(
                f"approvals/{approval['approval_id']}/approve",
                {"reviewer": REVIEWER, "comment": "Simulated fixture SQL review only"},
            )

        # ------------------------------------------------------- reflection input
        reflection = post(
            "reflections", {"experiment_run_ids": [experiments[0]["run"]["experiment_run_id"]]}
        )
        save("reflection", reflection)
        rid = reflection["run"]["reflection_run_id"]
        proposal = client.get(f"/api/v1/reflections/{rid}/proposals").json()[0]
        decision = post(
            f"reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "accepted_for_investigation",
                "reviewer": REVIEWER,
                "comment": "Fixture research, not production",
            },
        )
        save("proposal_decision", decision)

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
                "name": "phase6 synthetic series",
                "slices": slices,
                "label_definition_id": experiments[0]["run"]["label_definition_id"],
                "reference_slice_id": "slice-0",
            },
        )
        save("series", series)

        # -------------------------------------------- promotion -> metric version
        versions = {}
        for days in (60, 90):
            request = {
                "reflection_run_id": rid,
                "proposal_id": proposal["proposal_id"],
                "parameter_selection": {"window_days": days},
            }
            refinement = post("refinements", request)
            save(f"refinements/{days}d_request", request)
            save(f"refinements/{days}d_candidate", refinement)
            artifact = get(f"development/artifacts/{refinement['candidate']['artifact_id']}")
            (OUTPUT / f"invoice_amount_{days}d.sql").write_text(artifact["code"], encoding="utf-8")
            approve(refinement["candidate"])
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
                approve(slice_artifact)
            report = post(f"temporal-validations/{run_id}/run")
            save(f"{days}d_temporal_report", report)
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
            save(f"{days}d_promotion_review", decided)
            review_id = decided["promotion_review_id"]

            # A client cannot name its own version, IR or artifact.
            assert (
                client.post(
                    "/api/v1/metric-versions",
                    json={"promotion_review_id": review_id, "version": "9.9.9"},
                ).status_code
                == 422
            )
            version = post("metric-versions", {"promotion_review_id": review_id})
            save(f"{days}d_metric_version", version)
            versions[days] = version
            change = version["version_change"]
            assert version["version"] == EXPECTED[days], version["version"]
            print(
                "version",
                version["version"],
                version["metric_key"],
                change["kind"],
                "previous",
                change["previous_version"],
                "changed",
                sorted({item["field"] for item in change["changed_fields"]}),
            )

        definition = get("metrics/invoice_amount")
        save("definition", definition)
        listed = get("metrics/invoice_amount/versions")
        save("versions", listed)
        diff = get(f"metrics/invoice_amount/versions/{EXPECTED[60]}/compare/{EXPECTED[90]}")
        save("compare_1.0.0__2.0.0", diff)
        print("family", definition["metric_key"], [item["version"] for item in listed])
        print(
            "members",
            definition["member_metric_names"],
            "active",
            definition["active_version_id"],
        )
        print(f"classification {EXPECTED[60]} -> {EXPECTED[90]}:", diff["classification"])

        # ------------------------------------------------ governed release, twice
        releases = {}
        active_version = None
        for days in (60, 90):
            version = versions[days]
            # Production is refused while the environment is unverified.
            assert (
                client.post(
                    "/api/v1/releases",
                    json={
                        "metric_version_id": version["metric_version_id"],
                        "target_environment": "production",
                    },
                ).status_code
                == 409
            )
            release = post(
                "releases",
                {
                    "metric_version_id": version["metric_version_id"],
                    "target_environment": "staging",
                },
            )
            save(f"releases/{days}d_release", release)
            release_id = release["release_id"]
            assert release["status"] == "pending_staging_validation"
            assert release["production_deployed"] is False
            assert release["logical_activation_only"] is True
            # Review and activation are impossible before staging validation.
            assert client.post(f"/api/v1/releases/{release_id}/review").status_code == 409
            assert (
                client.post(f"/api/v1/releases/{release_id}/activate", json={}).status_code == 409
            )

            report = post(f"releases/{release_id}/validate")
            save(f"releases/{days}d_validation", report)
            assert report["release_eligibility"] == "eligible_for_release_review", report[
                "findings"
            ]
            assert report["production_deployed"] is False
            assert report["logical_activation_only"] is True
            shadow = report["shadow"]
            print(
                "release",
                version["version"],
                report["status"],
                "staging",
                report["staging"]["status"],
                "shadow",
                shadow["status"],
                "baseline",
                shadow["baseline"]["label"],
                f"({shadow['baseline']['role']})",
                "matched",
                shadow["matched_entities"],
                "changed",
                shadow["value_changed"],
                "loss",
                shadow["entity_loss_fraction"],
            )

            review = post(f"releases/{release_id}/review")
            assert review["previous_decisions"] == []
            decided = post(
                f"release-reviews/{review['release_review_id']}/decision",
                {"decision": "approved", "reviewer": REVIEWER, "comment": "Simulated release"},
            )
            save(f"releases/{days}d_review", decided)

            # Optimistic concurrency: the client's belief about the live version must hold.
            if active_version is None:
                stale = client.post(
                    f"/api/v1/releases/{release_id}/activate",
                    json={"expected_active_version": version["version"]},
                )
                assert stale.status_code == 409, stale.text
                assert stale.json()["error"]["code"] == "activation_conflict", stale.text
            activated = post(
                f"releases/{release_id}/activate",
                {"expected_active_version": active_version},
            )
            save(f"releases/{days}d_activation", activated)
            releases[days] = release_id
            active_version = activated["version"]
            active = get("metrics/invoice_amount/active")
            print("activated", activated["version"], "active pointer now", active["version"])
            assert activated["status"] == "active" and activated["production_deployed"] is False
            assert active["version"] == active_version

        # ------------------------------------------------------------- rollback
        target = versions[60]["version"]
        rollback = post(
            "metrics/invoice_amount/rollback",
            {
                "target_version": target,
                "reason": "Synthetic regression observed in the 90d version",
            },
        )
        save("rollback", rollback)
        assert (rollback["from_version"], rollback["to_version"]) == (active_version, target)
        decided = post(
            f"rollback-reviews/{rollback['rollback_review_id']}/decision",
            {"decision": "approved", "reviewer": REVIEWER, "comment": "Simulated rollback"},
        )
        save("rollback_decision", decided)
        active = get("metrics/invoice_amount/active")
        listed = get("metrics/invoice_amount/versions")
        # The replaced version is retired, never deleted; both releases stay readable.
        assert active["version"] == target
        assert [item["version"] for item in listed] == [target, versions[90]["version"]]
        assert [item["status"] for item in listed] == ["active", "retired"]
        assert get(f"releases/{releases[90]}")["status"] == "rolled_back"
        assert get(f"releases/{releases[60]}")["status"] == "active"
        print(
            "rolled back to",
            active["version"],
            "versions",
            [(item["version"], item["status"]) for item in listed],
        )

        events = get("metrics/invoice_amount/events")
        save("events", events)
        save("registry_summary", {"definition": definition, "versions": listed, "active": active})
        print("events:", [item["event_type"] for item in events])


if __name__ == "__main__":
    main()

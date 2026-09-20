"""Phase 7 demonstration: production handover, monitoring, alert, feedback, rollback.

Offline fixtures only. It never talks to Spark, MySQL or a real LLM, and the
simulated actors below are not authentication: they exist so the demo can show
that a production action is refused without a role.

The one question this answers: can an activated registry version be handed to a
production runtime through reviewed, evidenced, reversible steps, be watched,
raise an alert, produce feedback, and be rolled back by a human - while the
system keeps reporting honestly that no real production runtime exists here?
"""

import json
from datetime import timedelta
from pathlib import Path

from demo_phase3 import main as baseline_demo
from fastapi.testclient import TestClient

from airi.experiments.validation import dataset_checksum
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.main import create_app
from airi.production.adapters import MockProductionAdapter
from airi.production.identity import MockIdentityProvider, actor
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture
from airi.temporal.fixtures import temporal_fixture

OUTPUT = Path("examples/phase7")
DEMO_DB = Path(".demo/phase7_demo.db")
ACTORS = {
    "admin": actor("demo-admin", ("admin",)),
    "deployer": actor("demo-deployer", ("production_deployer",)),
    "rollback": actor("demo-rollback", ("rollback_approver",)),
    "reviewer": actor("demo-reviewer", ("metric_reviewer",)),
}
HEADERS = {name: {"x-airi-actor": name} for name in ACTORS}
ENVIRONMENT_ID = "prod_synth"


def build_app(settings):
    source, labels = refinement_fixture()
    runtime_store: dict = {}

    def adapter_factory(session, state, request_id=None):
        return MockProductionAdapter(session, state, request_id, runtime_store=runtime_store)

    settings.execution_mode = "mock"
    settings.production_adapter = "mock"
    app = create_app(
        settings,
        reflection_llm=MockWindowReflectionLLM(),
        query_executor=MockQueryExecutor(source, dataset_rows=labels),
        identity_provider=MockIdentityProvider(dict(ACTORS)),
        production_adapter_factory=adapter_factory,
    )
    return app, runtime_store


def main():
    # A registry is cumulative, so the demo always starts from an empty database.
    DEMO_DB.unlink(missing_ok=True)
    settings, experiments = baseline_demo(
        output_dir=str(OUTPUT / "baseline"),
        database_file="phase7_demo.db",
        fixture_factory=refinement_fixture,
        names=("invoice_amount_30d",),
    )
    app, runtime_store = build_app(settings)
    invoices, rows, periods = temporal_fixture(mode="stable")

    with TestClient(app) as client:

        def post(path, body=None, role="admin", expect=None):
            response = client.post("/api/v1/" + path, json=body, headers=HEADERS[role])
            if expect is not None:
                assert response.status_code == expect, response.text
            else:
                response.raise_for_status()
            return response.json()

        def get(path, role="admin", expect=None):
            response = client.get("/api/v1/" + path, headers=HEADERS[role])
            if expect is not None:
                assert response.status_code == expect, response.text
            else:
                response.raise_for_status()
            return response.json()

        def save(name, value):
            path = OUTPUT / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        def approve(artifact):
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
                {"reviewer": "synthetic-demo", "comment": "Fixture SQL review only"},
            )

        # ------------------------------------------------- registry lineage (v1, v2)
        reflection = post(
            "reflections",
            {"experiment_run_ids": [experiments[0]["run"]["experiment_run_id"]]},
        )
        rid = reflection["run"]["reflection_run_id"]
        proposal = get(f"reflections/{rid}/proposals")[0]
        post(
            f"reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "accepted_for_investigation",
                "reviewer": "synthetic-demo",
                "comment": "Fixture research, not production",
            },
        )
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
                "name": "phase7 synthetic series",
                "slices": slices,
                "label_definition_id": experiments[0]["run"]["label_definition_id"],
                "reference_slice_id": "slice-0",
            },
        )

        versions, releases, active_version = {}, {}, None
        for days in (60, 90):
            refinement = post(
                "refinements",
                {
                    "reflection_run_id": rid,
                    "proposal_id": proposal["proposal_id"],
                    "parameter_selection": {"window_days": days},
                },
            )
            artifact = get(f"development/artifacts/{refinement['candidate']['artifact_id']}")
            (OUTPUT / f"invoice_amount_{days}d.sql").write_text(artifact["code"], encoding="utf-8")
            approve(refinement["candidate"])
            refinement_id = refinement["run"]["refinement_run_id"]
            assert post(f"refinements/{refinement_id}/evaluate")["run"]["status"] == "completed"
            post(
                f"refinements/{refinement_id}/decision",
                {
                    "decision": "accept_candidate_for_further_validation",
                    "reviewer": "synthetic-demo",
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
            assert report["promotion_eligibility"] == "eligible_for_review", report["diagnostics"]
            review = post("promotion-reviews", {"temporal_validation_run_id": run_id})
            decided = post(
                f"promotion-reviews/{review['promotion_review_id']}/decision",
                {
                    "decision": "approved_for_versioning",
                    "reviewer": "synthetic-demo",
                    "comment": "Synthetic governance demonstration",
                },
            )
            version = post(
                "metric-versions", {"promotion_review_id": decided["promotion_review_id"]}
            )
            save(f"versions/{version['version']}", version)
            versions[days] = version

            release = post(
                "releases",
                {
                    "metric_version_id": version["metric_version_id"],
                    "target_environment": "staging",
                },
            )
            release_id = release["release_id"]
            validated = post(f"releases/{release_id}/validate")
            assert validated["release_eligibility"] == "eligible_for_release_review"
            release_review = post(f"releases/{release_id}/review")
            post(
                f"release-reviews/{release_review['release_review_id']}/decision",
                {"decision": "approved", "reviewer": "synthetic-demo", "comment": "Fixture"},
            )
            activated = post(
                f"releases/{release_id}/activate",
                {"expected_active_version": active_version},
            )
            releases[days] = release_id
            active_version = activated["version"]
        assert get("metrics/invoice_amount/active")["version"] == "2.0.0"

        # ------------------------------------------- production environment gate
        # Anonymous and unprivileged callers cannot declare a production target.
        assert (
            client.post(
                "/api/v1/production-environments",
                json={
                    "environment_id": ENVIRONMENT_ID,
                    "name": "Synthetic production environment",
                    "environment_kind": "production",
                    "execution_mode": "mock",
                    "deployment_enabled": True,
                    "synthetic_profile": True,
                },
            ).status_code
            == 401
        )
        profile = post(
            "production-environments",
            {
                "environment_id": ENVIRONMENT_ID,
                "name": "Synthetic production environment",
                "environment_kind": "production",
                "execution_mode": "mock",
                "deployment_enabled": True,
                "synthetic_profile": True,
            },
            role="admin",
        )
        save("environment_profile", profile)
        assert profile["verified"] is True
        assert profile["synthetic_profile"] is True
        print(
            "environment",
            profile["environment_id"],
            "verified",
            profile["verification_source"],
            "kind",
            profile["environment_kind"],
            "deployment_enabled",
            profile["deployment_enabled"],
        )

        # A missing production environment is refused before anything is packaged.
        missing = client.post(
            "/api/v1/production-deployments",
            json={"release_id": releases[90], "environment_id": "prod_absent"},
            headers=HEADERS["deployer"],
        )
        assert missing.status_code == 404, missing.text
        print("deployment without a verified environment ->", missing.status_code)

        # ------------------------------------------------------ deployment flow
        created = post(
            "production-deployments",
            {"release_id": releases[90], "environment_id": ENVIRONMENT_ID},
            role="deployer",
        )
        deployment_id = created["deployment_id"]
        save("deployment_created", created)
        assert created["status"] == "created"
        assert created["production_deployed"] is False
        # The client named only a release and an environment; SQL came from lineage.
        package = created["package"]
        assert package["sql"].strip()
        assert package["release_id"] == releases[90]
        assert package["metric_version_id"] == versions[90]["metric_version_id"]
        assert package["environment_id"] == ENVIRONMENT_ID
        assert len(package["content_hash"]) == 64

        # created -> deploy is not a deployment flow.
        assert (
            client.post(
                f"/api/v1/production-deployments/{deployment_id}/deploy",
                headers=HEADERS["deployer"],
            ).status_code
            == 409
        )

        preflight = post(f"production-deployments/{deployment_id}/preflight", role="deployer")
        save("preflight", preflight)
        assert preflight["read_only_preflight"] is True
        assert preflight["production_deployed"] is False
        print(
            "preflight",
            preflight["status"],
            "checks",
            [(item["name"], item["status"]) for item in preflight["checks"]],
        )

        shadow = post(f"production-deployments/{deployment_id}/shadow", role="deployer")
        save("shadow", shadow)
        assert shadow["write_business_output"] is False
        assert shadow["production_deployed"] is False
        print(
            "shadow",
            shadow["status"],
            "rows",
            shadow["row_count"],
            "entities",
            shadow["entity_count"],
            "coverage",
            shadow["coverage"],
            "runtime",
            shadow["runtime_mode"],
        )

        review = post(f"production-deployments/{deployment_id}/review", role="deployer")
        # A metric reviewer may not approve a production deployment.
        assert (
            client.post(
                f"/api/v1/production-deployment-reviews/{review['deployment_review_id']}/decision",
                json={"decision": "approved", "comment": "wrong role"},
                headers=HEADERS["reviewer"],
            ).status_code
            == 403
        )
        decided = post(
            f"production-deployment-reviews/{review['deployment_review_id']}/decision",
            {
                "decision": "approved",
                "comment": "Simulated human deployment approval on synthetic evidence",
            },
            role="deployer",
        )
        save("deployment_review", decided)

        deployed = post(f"production-deployments/{deployment_id}/deploy", role="deployer")
        save("deployment_deployed", deployed)
        assert deployed["status"] == "deployed"
        # The central honesty rule: a mock provider never becomes a production deployment.
        assert deployed["production_deployed"] is False
        assert deployed["runtime_identity_verified"] is False
        assert deployed["runtime_mode"] == "mock"
        assert deployed["missing_evidence"]
        print(
            "deployed",
            deployed["status"],
            "production_deployed",
            deployed["production_deployed"],
            "missing_evidence",
            deployed["missing_evidence"],
        )

        # Reconciliation before any anomaly: registry pointer and runtime agree.
        reconciled = get(f"production-deployments/{deployment_id}/reconcile")
        save("reconciliation_consistent", reconciled)
        assert reconciled["status"] == "consistent"
        assert reconciled["automatic_correction"] is False
        print("reconciled", reconciled["status"], "registry", reconciled["registry_active_version"])

        baseline = deployed["monitoring_baseline"]
        assert baseline["source"] == "production_shadow"

        def snapshot_payload(observation_time, coverage, distribution, execution=None):
            return {
                "deployment_id": deployment_id,
                "observation_time": observation_time,
                "execution": execution
                or {
                    "status": "success",
                    "duration_ms": 150,
                    "row_count": baseline["row_count"],
                },
                "quality": {
                    "coverage": coverage,
                    "null_rate": baseline["null_rate"],
                    "duplicate_rate": baseline["duplicate_rate"],
                },
                "distribution": distribution,
                "source_system": "synthetic-runtime-monitor",
                "source_event_id": f"synthetic-{observation_time}",
            }

        # ------------------------------------------------------------- anomaly
        bins = [dict(item) for item in baseline["distribution"]["bins"]]
        total = sum(item["count"] for item in bins)
        shifted = [dict(item) for item in bins]
        for index, item in enumerate(shifted):
            item["count"] = total if index == 0 else 0
        payload = snapshot_payload(
            "2026-02-01T00:00:00+00:00",
            max(0.0, (baseline["coverage"] or 1.0) - 0.4),
            {"bins": shifted, "total": total},
        )
        first = post("monitoring-snapshots", payload, role="reviewer")
        save("monitoring_snapshot_anomaly", first)
        assert first["snapshot"]["psi"] is not None
        assert first["snapshot"]["psi"] > 0.35
        assert first["created_alert_ids"]
        for alert in first["alerts"]:
            print(
                "alert",
                alert["type"],
                alert["severity"],
                alert["recommended_action"],
                "automatic_action_taken",
                alert["automatic_action_taken"],
            )
        critical = [item for item in first["alerts"] if item["severity"] == "critical"]
        assert critical, first["alerts"]
        assert {item["type"] for item in first["alerts"]} >= {
            "coverage_drop",
            "population_shift",
        }

        # The deployment is now under monitoring, and the registry pointer did not move.
        assert get(f"production-deployments/{deployment_id}")["status"] == "monitoring"
        assert get("metrics/invoice_amount/active")["version"] == "2.0.0"

        # The same anomaly again: the alert is deduplicated, not duplicated.
        second = post(
            "monitoring-snapshots",
            {**payload, "observation_time": "2026-02-01T01:00:00+00:00"},
            role="reviewer",
        )
        assert second["created_alert_ids"] == []
        listed = get(f"metric-alerts?deployment_id={deployment_id}")
        save("alerts", listed)
        deduped = next(item for item in listed if item["type"] == "coverage_drop")
        assert deduped["occurrences"] == 2
        print("alert dedup -> occurrences", deduped["occurrences"], "alerts", len(listed))

        # Triaging an alert is not a rollback: nothing is executed.
        triaged = post(
            f"metric-alerts/{deduped['alert_id']}/decision",
            {"decision": "acknowledge", "comment": "Investigating with the data team"},
            role="rollback",
        )
        assert triaged["status"] == "acknowledged"
        assert get(f"production-deployments/{deployment_id}")["status"] == "monitoring"
        print("alert triaged", triaged["status"], "- deployment still", "monitoring")

        # ------------------------------------------------------------ feedback
        from_alert = post(f"metric-alerts/{deduped['alert_id']}/feedback", role="reviewer")
        save("feedback_event", from_alert)
        assert from_alert["record_kind"] == "fact"
        assert from_alert["interpretation"] is None
        assert from_alert["automatic_research_started"] is False
        print(
            "feedback",
            from_alert["type"],
            "source",
            from_alert["source"],
            "interpretation",
            from_alert["interpretation"],
        )

        def registered_versions():
            return sum(
                item["event_type"] == "version_registered"
                for item in get("metrics/invoice_amount/events")
            )

        versions_before = registered_versions()
        research = post(
            f"metric-feedback-events/{from_alert['feedback_event_id']}/research-requests",
            {
                "request_type": "revalidation",
                "rationale": "Coverage moved after cutover; revalidate before any rewrite",
            },
            role="reviewer",
        )
        decided_research = post(
            f"feedback-research-requests/{research['research_request_id']}/decision",
            {"decision": "approved", "comment": "Open a revalidation, not a metric rewrite"},
            role="reviewer",
        )
        save("feedback_research_request", decided_research)
        assert decided_research["automatic_reflection"] is False
        # A human asked for research; the system rewrote no metric by itself.
        assert registered_versions() == versions_before == 2
        print(
            "research request",
            decided_research["decision"],
            "automatic_reflection",
            decided_research["automatic_reflection"],
            "versions still",
            registered_versions(),
        )

        # --------------------------------------------------------- mismatch drill
        runtime_store[deployment_id] = {
            "metric_version_id": versions[60]["metric_version_id"],
            "state": "active",
        }
        mismatch = get(f"production-deployments/{deployment_id}/reconcile")
        save("reconciliation_mismatch", mismatch)
        assert mismatch["status"] == "mismatch"
        assert mismatch["automatic_correction"] is False
        # Detection only: neither the registry pointer nor the deployment moved.
        assert get("metrics/invoice_amount/active")["version"] == "2.0.0"
        assert get(f"production-deployments/{deployment_id}")["status"] == "monitoring"
        print(
            "mismatch detected: registry",
            mismatch["registry_active_version"],
            "runtime",
            mismatch["production_active_version_id"],
            "- repaired",
            mismatch["automatic_correction"],
        )

        # ------------------------------------------------------ human rollback
        rollback = post(
            f"production-deployments/{deployment_id}/rollback",
            {
                "target_version": "1.0.0",
                "reason": "Synthetic coverage regression after cutover",
                "alert_ids": [item["alert_id"] for item in critical],
            },
            role="deployer",
        )
        save("production_rollback_request", rollback)
        assert rollback["automatic"] is False
        # Only a rollback approver may approve it.
        assert (
            client.post(
                f"/api/v1/production-rollback-reviews/{rollback['rollback_review_id']}/decision",
                json={"decision": "approved", "comment": "wrong role"},
                headers=HEADERS["deployer"],
            ).status_code
            == 403
        )
        approved = post(
            f"production-rollback-reviews/{rollback['rollback_review_id']}/decision",
            {"decision": "approved", "comment": "Simulated human rollback approval"},
            role="rollback",
        )
        save("production_rollback_decision", approved)
        assert approved["production_rollback_status"] == "succeeded"
        assert approved["registry_reconciliation_status"] == "reconciled"
        assert get("metrics/invoice_amount/active")["version"] == "1.0.0"
        assert get(f"production-deployments/{deployment_id}")["status"] == "rolled_back"
        versions_after = get("metrics/invoice_amount/versions")
        # Rollback moves a pointer; it never deletes a version.
        assert [item["version"] for item in versions_after] == ["1.0.0", "2.0.0"]
        print(
            "rolled back to",
            get("metrics/invoice_amount/active")["version"],
            "versions",
            [(item["version"], item["status"]) for item in versions_after],
        )

        events = get("metrics/invoice_amount/events")
        save("events", events)
        save(
            "production_summary",
            {
                "environment": profile,
                "deployment": get(f"production-deployments/{deployment_id}"),
                "alerts": get(f"metric-alerts?deployment_id={deployment_id}"),
                "feedback": get("metric-feedback-events"),
                "active_version": get("metrics/invoice_amount/active"),
            },
        )
        print("production events:", [item["event_type"] for item in events][-8:])


if __name__ == "__main__":
    main()

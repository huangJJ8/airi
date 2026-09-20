"""Synthetic Phase 8 Governance Demo.

This is deliberately NOT a "Real Production Verification Demo". It runs entirely
on offline fixtures and a mock provider, so it can never verify a real runtime -
and it says so at every step. What it demonstrates is the *evidence layer* that
Phase 8 puts around a production path, on top of an integration that is known to
be synthetic:

    Configured            !=  Verified
    Connected             !=  Authenticated
    Authenticated         !=  Authorized
    Deploy returned ok    !=  Runtime verified
    Monitoring received   !=  Telemetry trusted
    Mismatch detected     !=  Mismatch fixed
    Recovery executed     !=  Convergence verified

Every "verified" below is scoped: telemetry is trusted because a human attested
the producer, the recovery converged because the runtime was re-observed - never
because a call returned success.
"""

import json
from datetime import UTC, datetime, timedelta
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

OUTPUT = Path("examples/phase8")
DEMO_DB = Path(".demo/phase8_demo.db")
ACTORS = {
    "admin": actor("demo-admin", ("admin",)),
    "deployer": actor("demo-deployer", ("production_deployer",)),
    "rollback": actor("demo-rollback", ("rollback_approver",)),
    "reviewer": actor("demo-reviewer", ("metric_reviewer",)),
}
HEADERS = {name: {"x-airi-actor": name} for name in ACTORS}
ENVIRONMENT_ID = "prod_synth"
TELEMETRY_SOURCE_ID = "synthetic_monitor"
# A recovery decision is only ever one of the actions the plan actually offered.
DECISION_FOR_ACTION = {
    "registry_to_runtime": "align_runtime_to_registry",
    "runtime_to_registry": "align_registry_to_runtime",
    "manual_investigation": "keep_mismatch_for_investigation",
}


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
    DEMO_DB.unlink(missing_ok=True)
    settings, experiments = baseline_demo(
        output_dir=str(OUTPUT / "baseline"),
        database_file="phase8_demo.db",
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
            elif response.status_code >= 400:
                raise AssertionError(f"POST {path} -> {response.status_code}: {response.text}")
            return response.json()

        def get(path, role="admin", expect=None):
            response = client.get("/api/v1/" + path, headers=HEADERS[role])
            if expect is not None:
                assert response.status_code == expect, response.text
            elif response.status_code >= 400:
                raise AssertionError(f"GET {path} -> {response.status_code}: {response.text}")
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

        print("=" * 78)
        print("Synthetic Phase 8 Governance Demo")
        print("Not a real production verification: fixtures + mock provider only.")
        print("=" * 78)

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
                "name": "phase8 synthetic series",
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
            # The candidate artifact must be retrievable before it can be approved.
            get(f"development/artifacts/{refinement['candidate']['artifact_id']}")
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

        # ============================================ 8A: configured != verified
        print("\n--- 8A  Configured != Verified --------------------------------")
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
        print(
            "environment registered:",
            profile["environment_id"],
            "verified_by=",
            profile["verification_source"],
            "synthetic=",
            profile["synthetic_profile"],
        )
        # A synthetic profile has no runtime to observe, and AIRI refuses to
        # invent one. This is the whole point of the fingerprint.
        probe = client.get(
            f"/api/v1/production-environments/{ENVIRONMENT_ID}/runtime-probe",
            headers=HEADERS["admin"],
        )
        fingerprint = client.post(
            f"/api/v1/production-environments/{ENVIRONMENT_ID}/fingerprint",
            headers=HEADERS["admin"],
        )
        assert probe.status_code == 409, probe.text
        assert fingerprint.status_code == 409, fingerprint.text
        print(
            "runtime probe      ->",
            probe.status_code,
            probe.json()["error"]["code"],
        )
        print(
            "runtime fingerprint->",
            fingerprint.status_code,
            fingerprint.json()["error"]["code"],
        )
        print("=> the environment is 'verified' as a synthetic profile, and 0 bytes of")
        print("   runtime evidence exist. Configured is not verified.")

        # ==================================== 8C: received != trusted telemetry
        print("\n--- 8C  Monitoring received != Telemetry trusted -------------")
        source = post(
            "telemetry-sources",
            {
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
                "source_system": "synthetic_monitor",
                "environment_id": ENVIRONMENT_ID,
                "auth_identity": "monitor@airi.invalid",
            },
            role="admin",
        )
        save("telemetry_source_registered", source)
        assert source["verified"] is False
        print(
            "producer registered:", source["telemetry_source_id"], "verified=", source["verified"]
        )

        # ------------------------------------------------------ deployment flow
        created = post(
            "production-deployments",
            {"release_id": releases[90], "environment_id": ENVIRONMENT_ID},
            role="deployer",
        )
        deployment_id = created["deployment_id"]
        assert (
            client.post(
                f"/api/v1/production-deployments/{deployment_id}/preflight",
                headers=HEADERS["deployer"],
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/production-deployments/{deployment_id}/shadow",
                headers=HEADERS["deployer"],
            ).status_code
            == 200
        )
        review = post(f"production-deployments/{deployment_id}/review", role="deployer")
        post(
            f"production-deployment-reviews/{review['deployment_review_id']}/decision",
            {
                "decision": "approved",
                "comment": "Simulated human deployment approval on synthetic evidence",
            },
            role="deployer",
        )
        deployed = post(f"production-deployments/{deployment_id}/deploy", role="deployer")
        save("deployment_deployed", deployed)
        assert deployed["production_deployed"] is False
        print(
            "deployed status=",
            deployed["status"],
            "production_deployed=",
            deployed["production_deployed"],
            "(mock provider never becomes a production deployment)",
        )

        # 8B1: the double-confirmed deployment record.
        evidence = get(f"production-deployments/{deployment_id}/deployment-evidence")
        save("deployment_evidence", evidence)
        record = evidence[0]
        print(
            "deployment evidence: deploy_status=",
            record["deploy_status"],
            "status_confirmed=",
            record["status_confirmed"],
            "production_deployed=",
            record["production_deployed"],
        )
        print("=> 'deploy' answered deployed AND 'status' re-read active. Both facts are stored;")
        print("   the claim is still false because no authoritative runtime was involved.")

        baseline = deployed["monitoring_baseline"]

        def snapshot_payload(
            observation_time, coverage, distribution, source_event_id, execution=None
        ):
            return {
                "deployment_id": deployment_id,
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
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
                "source_system": "synthetic_monitor",
                "source_event_id": source_event_id,
            }

        now = datetime.now(UTC)
        first = post(
            "monitoring-snapshots",
            snapshot_payload(
                now.isoformat(), baseline["coverage"], baseline["distribution"], "phase8-1"
            ),
            role="reviewer",
        )
        save("snapshot_untrusted", first)
        snapshot = first["snapshot"]
        print(
            "snapshot ingested: trust=",
            snapshot["telemetry_trust"],
            "freshness=",
            snapshot["telemetry_freshness"],
            "lag_hours=",
            round(snapshot["observation_lag_hours"], 4),
        )
        print("warnings:", first["warnings"])

        # Idempotency: the same producer event is one fact, not two.
        duplicate = post(
            "monitoring-snapshots",
            snapshot_payload(
                now.isoformat(), baseline["coverage"], baseline["distribution"], "phase8-1"
            ),
            role="reviewer",
        )
        assert duplicate["snapshot"]["monitoring_snapshot_id"] == snapshot["monitoring_snapshot_id"]
        print("replay of the same source_event_id ->", duplicate["warnings"])

        # A backdated observation delivered now is stale history, never current.
        late = post(
            "monitoring-snapshots",
            snapshot_payload(
                (now - timedelta(days=3)).isoformat(),
                baseline["coverage"],
                baseline["distribution"],
                "phase8-backdated",
            ),
            role="reviewer",
        )
        print(
            "backdated observation 3 days old -> freshness=",
            late["snapshot"]["telemetry_freshness"],
            "lag_hours=",
            round(late["snapshot"]["observation_lag_hours"], 1),
        )

        # A human attests the producer, and only then is telemetry trusted.
        verified = post(
            f"telemetry-sources/{TELEMETRY_SOURCE_ID}/verify",
            {"note": "operator attested the producer identity out of band", "synthetic": False},
            role="admin",
        )
        save("telemetry_source_verified", verified)
        assert verified["verified"] is True
        trusted = post(
            "monitoring-snapshots",
            snapshot_payload(
                now.isoformat(), baseline["coverage"], baseline["distribution"], "phase8-2"
            ),
            role="reviewer",
        )
        print(
            "after attestation -> trust=",
            trusted["snapshot"]["telemetry_trust"],
            "warnings=",
            trusted["warnings"],
        )

        # 8C: an expectation declares when telemetry should arrive. It schedules nothing.
        expectation = post(
            f"production-deployments/{deployment_id}/monitoring-expectation",
            {
                "deployment_id": deployment_id,
                "expected_interval_hours": 24,
                "grace_hours": 6,
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
            },
            role="admin",
        )
        save("monitoring_expectation", expectation)
        status = get(f"production-deployments/{deployment_id}/monitoring-expectation/status")
        save("monitoring_expectation_status", status)
        print(
            "expectation every",
            expectation["expected_interval_hours"],
            "h (+/-",
            expectation["grace_hours"],
            "h) -> status",
            status["status"],
            "- no scheduler was created",
        )

        # ================================= 8B: identity evidence on decisions
        print("\n--- 8B  Trusted identity evidence ----------------------------")
        recorded = post(f"production-deployments/{deployment_id}/verification", role="admin")
        save("verification_report", recorded)
        sections = {item["name"]: item["status"] for item in recorded["sections"]}
        print("verification overall:", recorded["overall"])
        print("sections:", json.dumps(sections, indent=None))
        print("blocking:", recorded["blocking"])
        print("not_verified:", recorded["not_verified"])
        print("=> one boolean would hide this. rollback and reconciliation are separate")
        print("   verdicts from the environment, identity and telemetry sections.")
        # The decision was recorded under a local synthetic identity, and the report says so.
        assert sections["identity"] == "not_verified", sections

        # ================================= 8E: rollback evidence + preflight
        print("\n--- 8E  Recovery executed != Convergence verified ------------")
        rollback = post(
            f"production-deployments/{deployment_id}/rollback",
            {
                "target_version": "1.0.0",
                "reason": "Synthetic regression drill after cutover",
            },
            role="deployer",
        )
        save("production_rollback_request", rollback)
        print(
            "rollback preflight:",
            rollback["preflight"]["status"],
            "missing=",
            rollback["preflight"]["missing_evidence"],
        )
        print("=> the preflight runs *before* a human is asked, so an unbounded recovery never")
        print("   reaches the approver, and whatever it could not verify is named out loud.")
        approved = post(
            f"production-rollback-reviews/{rollback['rollback_review_id']}/decision",
            {"decision": "approved", "comment": "Simulated human rollback approval"},
            role="rollback",
        )
        save("production_rollback_decision", approved)
        print(
            "rollback status=",
            approved["production_rollback_status"],
            "registry=",
            approved["registry_reconciliation_status"],
            "runtime_verified=",
            approved["runtime_verified"],
        )
        assert approved["runtime_verified"] is True
        print("=> runtime_verified is recorded from a second provider read, not the reply.")

        # ================================ 8D: detected != fixed, auditable
        print("\n--- 8D  Mismatch detected != Mismatch fixed ------------------")
        # The synthetic runtime is made to drift onto a version the registry never
        # produced. Nothing may be "aligned" to a version the registry does not own.
        runtime_store[deployment_id] = {"metric_version_id": "ghost_version", "state": "active"}
        mismatch = get(f"production-deployments/{deployment_id}/reconcile")
        save("reconciliation_mismatch", mismatch)
        assert mismatch["status"] == "mismatch"
        assert mismatch["automatic_correction"] is False
        print(
            "detected: registry",
            mismatch["registry_active_version"],
            "runtime",
            # A version the registry never produced has no name, only an id. The
            # report says so instead of inventing a label for it.
            mismatch["production_active_version"] or mismatch["production_active_version_id"],
            "- repaired automatically:",
            mismatch["automatic_correction"],
        )

        plan = post(f"production-deployments/{deployment_id}/reconciliation-plans", role="admin")
        save("reconciliation_plan_investigate", plan)
        print("plan verdict=", plan["verdict"], "possible=", plan["possible_actions"])
        print("recommended=", plan["recommended_action"], "(a recommendation, not a verdict)")
        print("rationale:", plan["rationale"])

        review = post(
            f"reconciliation-plans/{plan['reconciliation_plan_id']}/review",
            {"comment": "A runtime version the registry never produced cannot be adopted"},
            role="admin",
        )
        held = post(
            f"reconciliation-reviews/{review['reconciliation_review_id']}/decision",
            {
                "decision": "keep_mismatch_for_investigation",
                "comment": "Escalate to the platform team",
            },
            role="admin",
        )
        save("reconciliation_result_investigate", held)
        assert held["final_status"] == "reconciliation_not_executed"
        print(
            "kept open ->",
            held["execution_status"],
            held["final_status"],
            "- the registry pointer never adopted a version it did not produce",
        )

        # A second mismatch, this time against a version the registry does know.
        runtime_store[deployment_id] = {
            "metric_version_id": versions[90]["metric_version_id"],
            "state": "active",
        }
        plan = post(f"production-deployments/{deployment_id}/reconciliation-plans", role="admin")
        save("reconciliation_plan_align", plan)
        print(
            "\nplan verdict=",
            plan["verdict"],
            "possible=",
            plan["possible_actions"],
            "recommended=",
            plan["recommended_action"],
        )
        review = post(
            f"reconciliation-plans/{plan['reconciliation_plan_id']}/review",
            {"comment": "Open the recovery for a human decision"},
            role="admin",
        )
        decided = post(
            f"reconciliation-reviews/{review['reconciliation_review_id']}/decision",
            {
                # The human picks from what the plan actually offered. An action the
                # plan never listed is refused outright, not performed.
                "decision": DECISION_FOR_ACTION[plan["recommended_action"]],
                "comment": "The runtime reports a version the registry knows",
            },
            role="admin",
        )
        save("reconciliation_result", decided)
        print(
            "executed:",
            decided["execution_status"],
            "convergence:",
            decided["convergence_status"],
            "final:",
            decided["final_status"],
        )
        print(
            "=> convergence was confirmed by re-observing the runtime, not by trusting the"
            " recovery's return value."
        )

        final = post(f"production-deployments/{deployment_id}/verification", role="admin")
        save("verification_report_final", final)
        final_sections = {item["name"]: item["status"] for item in final["sections"]}
        print(
            "\nfinal verification overall=",
            final["overall"],
            "production_runtime_verified=",
            final["production_runtime_verified"],
        )
        print(
            "rollback:",
            final_sections["rollback"],
            "reconciliation:",
            final_sections["reconciliation"],
        )
        print("blocking:", final["blocking"])
        print("=> rollback is verified because the runtime was re-read after the recovery.")
        print("   environment / identity stay not_verified, and a synthetic profile cannot")
        print("   change that by passing more often.")

        events = get("metrics/invoice_amount/events")
        save("events", events)
        phase8_events = [
            item["event_type"]
            for item in events
            if item["event_type"]
            in {
                "production_environment_verified",
                "runtime_identity_verified",
                "production_deployment_evidence_recorded",
                "telemetry_source_registered",
                "telemetry_source_verified",
                "monitoring_expectation_registered",
                "production_verification_recorded",
                "production_rollback_preflight_validated",
                "reconciliation_planned",
                "reconciliation_approved",
                "reconciliation_executed",
                "reconciliation_verified",
            }
        ]
        print("\nPhase 8 audit events:", phase8_events)
        print("\nArtifacts written to", OUTPUT)
        print("Reminder: nothing in this run verified a real production runtime.")


if __name__ == "__main__":
    main()

"""Synthetic Phase 9 Operational Convergence Demo.

The name is the first honest sentence in this file: it runs on offline fixtures
and a Mock provider, so it can never verify a real runtime. What it demonstrates
is the layer Phase 9 adds *around* an integration that is known to be synthetic -
operational reliability and governed convergence, not autonomy.

Phase 8 proved a "done" claim can be refused. Phase 9 answers the question Phase 8
left open: when the registry, the runtime and the *desired* state disagree, which
of them is wrong, and what is a system allowed to do about it by itself?

    Desired state     !=  Registry pointer   !=  Runtime state
    Target already met !=  Conflict
    Alert recorded    !=  Alert delivered
    Nothing checked   !=  Checked and fine
    Row present       !=  Row verified

Three rules run through the whole demo:

* nothing converges without re-observing the runtime, and nothing is "repaired"
  by a read;
* a convergence attempt that finds the goal already met reports a *proven no-op*
  rather than a conflict - that is the Phase 8 rollback bug, fixed in 9A;
* AIRI still owns the question, never the clock. Evaluating telemetry is an
  explicit command an external scheduler calls; no timer is created here.
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
from airi.production.notifications import MockNotificationSink
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture
from airi.temporal.fixtures import temporal_fixture

OUTPUT = Path("examples/phase9")
DEMO_DB = Path(".demo/phase9_demo.db")
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
        database_file="phase9_demo.db",
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
        print("Synthetic Phase 9 Operational Convergence Demo")
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
                "name": "phase9 synthetic series",
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

        # --------------------------------------------------- environment + producer
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
        post(
            "telemetry-sources",
            {
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
                "source_system": "synthetic_monitor",
                "environment_id": ENVIRONMENT_ID,
                "auth_identity": "monitor@airi.invalid",
            },
            role="admin",
        )
        # A human attests the producer. Phase 9 needs telemetry it can reason about;
        # the *trust* ceremony itself is Phase 8 and is not re-litigated here.
        post(
            f"telemetry-sources/{TELEMETRY_SOURCE_ID}/verify",
            {"note": "operator attested the producer identity out of band", "synthetic": False},
            role="admin",
        )

        # 9B comes first on purpose: a producer that has never delivered anything is
        # the cleanest way to see that "silent" and "late" are different problems.
        print("\n--- 9B  A silent producer is not a late one --------------------")
        health_before = get(f"telemetry-sources/{TELEMETRY_SOURCE_ID}/health")
        save("telemetry_health_before", health_before)
        print(
            "producer health:",
            health_before["status"],
            "verified=",
            health_before["verified"],
            "synthetic=",
            health_before["synthetic"],
            "snapshots=",
            health_before["snapshot_count"],
        )
        print("notifies_metric_health=", health_before["metric_health_implied"])
        print("=> 'silent' is a fact about a pipeline. It is never a verdict about the metric")

        # ------------------------------------------------------ deployment → live
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
        print(
            "\ndeployed status=",
            deployed["status"],
            "production_deployed=",
            deployed["production_deployed"],
        )
        baseline = deployed["monitoring_baseline"]

        # ==================================== 9B: the check, and the alert it may raise
        print("\n--- 9B  Nobody checked != Checked and fine --------------------")
        expectation = post(
            f"production-deployments/{deployment_id}/monitoring-expectation",
            {
                "deployment_id": deployment_id,
                "expected_interval_hours": 1,
                "grace_hours": 0,
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
            },
            role="admin",
        )
        save("monitoring_expectation", expectation)

        def snapshot_payload(observation_time, source_event_id):
            return {
                "deployment_id": deployment_id,
                "telemetry_source_id": TELEMETRY_SOURCE_ID,
                "observation_time": observation_time,
                "execution": {
                    "status": "success",
                    "duration_ms": 150,
                    "row_count": baseline["row_count"],
                },
                "quality": {
                    "coverage": baseline["coverage"],
                    "null_rate": baseline["null_rate"],
                    "duplicate_rate": baseline["duplicate_rate"],
                },
                "distribution": baseline["distribution"],
                "source_system": "synthetic_monitor",
                "source_event_id": source_event_id,
            }

        # Two hours of lag: stale, not late. The transport is not the story here, so
        # the only alert that can appear is the one the expectation itself asks for.
        now = datetime.now(UTC)
        post(
            "monitoring-snapshots",
            snapshot_payload((now - timedelta(hours=2)).isoformat(), "phase9-snap-1"),
            role="reviewer",
        )
        status = get(f"production-deployments/{deployment_id}/monitoring-expectation/status")
        save("monitoring_expectation_status", status)
        print(
            "expectation status=",
            status["status"],
            "findings=",
            status["findings"],
            "count=",
            status["snapshot_count"],
        )
        print(
            "alerts so far:",
            [item["type"] for item in get(f"metric-alerts?deployment_id={deployment_id}")],
            "- reading a status is advisory, it opens nothing",
        )

        run = post(f"monitoring-expectations/evaluate?deployment_id={deployment_id}", role="admin")
        save("evaluation_run", run)
        print(
            "\nevaluate run:",
            run["evaluation_run_id"][:8],
            "by=",
            run["evaluated_by"],
            "expectations=",
            run["expectation_count"],
            "overdue=",
            run["overdue_count"],
            "missing=",
            run["telemetry_missing_count"],
        )
        print("=> the run is evidence: 'nobody checked' and 'checked and fine' differ")
        alerts = get(f"metric-alerts?deployment_id={deployment_id}")
        save("alerts", alerts)
        print(
            "alert raised:",
            alerts[0]["type"],
            "severity=",
            alerts[0]["severity"],
            "recommended=",
            alerts[0]["recommended_action"],
        )

        # The same missed window is the same alert, counted - not a second alert.
        repeat = post(
            f"monitoring-expectations/evaluate?deployment_id={deployment_id}", role="admin"
        )
        alerts = get(f"metric-alerts?deployment_id={deployment_id}")
        print(
            "re-evaluating the same window -> alerts=",
            len(alerts),
            "occurrences=",
            alerts[0]["occurrences"],
            "- same window, same alert",
        )
        assert len(alerts) == 1 and alerts[0]["occurrences"] == 2
        assert repeat["created_alert_ids"] == run["created_alert_ids"]

        alert_id = alerts[0]["alert_id"]
        health_after = get(f"telemetry-sources/{TELEMETRY_SOURCE_ID}/health")
        save("telemetry_health_after", health_after)
        print(
            "\nproducer health now:",
            health_after["status"],
            "overdue=",
            health_after["overdue_deployment_ids"] == [deployment_id],
            "silent=",
            health_after["silent_deployment_ids"],
        )
        print("=> 'silent' became 'degraded': a delivery arrived and missed its window")

        # ==================================== 9B: an alert is not a notification
        print("\n--- 9B  An alert is not a notification -----------------------")
        default_delivery = get(f"metric-alerts/{alert_id}/notifications")
        save("notifications_disabled", default_delivery)
        print(
            "default sink:",
            [item["status"] for item in default_delivery],
            "sink=",
            default_delivery[0]["sink"],
            "- nobody was told, and the attempt says exactly that",
        )

        # An explicit demo injection. Never selectable through configuration.
        app.state.notification_sink = MockNotificationSink()
        delivered = post(f"metric-alerts/{alert_id}/notifications", role="admin")
        print(
            "with a mock sink:",
            delivered["status"],
            "sink=",
            delivered["sink"],
            "replayed=",
            delivered["replayed"],
        )
        replay = post(f"metric-alerts/{alert_id}/notifications", role="admin")
        print(
            "same call again -> replayed=",
            replay["replayed"],
            "- the earlier delivery is returned, the alert is not sent twice",
        )
        assert replay["replayed"] is True
        resent = post(f"metric-alerts/{alert_id}/notifications?resend=true", role="admin")
        print("explicit resend -> replayed=", resent["replayed"], "- a deliberate second send")

        app.state.notification_sink = MockNotificationSink(fail=True)
        failed = post(f"metric-alerts/{alert_id}/notifications?resend=true", role="admin")
        notifications = get(f"metric-alerts/{alert_id}/notifications")
        save("notifications", notifications)
        print(
            "a broken sink:",
            failed["status"],
            "category=",
            failed["failure_category"],
            "-> alert status=",
            get(f"metric-alerts/{alert_id}")["status"],
        )
        assert failed["status"] == "failed"
        assert get(f"metric-alerts/{alert_id}")["status"] == "open"
        print("=> a chat platform being down never undoes the alert it was carrying")

        # ==================================================== 9D: three states apart
        print("\n--- 9D  Workflow != Runtime != Convergence --------------------")
        # The runtime is made to report the *previous* version while the registry and
        # the deployment record both say v2. Three facts, three axes, one view.
        runtime_store[deployment_id] = {
            "metric_version_id": versions[60]["metric_version_id"],
            "state": "active",
        }
        health = get(f"production-deployments/{deployment_id}/health")
        save("health_three_axes", health)
        print(
            "workflow=",
            health["workflow_status"],
            "runtime=",
            health["runtime_state"],
            "convergence=",
            health["convergence_state"],
            "-> health=",
            health["health"],
        )
        print("reasons:", health["reasons"])
        # The workflow axis says the deployment is live and receiving telemetry; the
        # runtime axis says v1. Neither is allowed to stand in for the other.
        assert health["workflow_status"] in ("deployed", "monitoring")
        assert health["convergence_state"] == "mismatch"
        assert health["drives_execution"] is False

        convergence = get(f"production-deployments/{deployment_id}/convergence")
        save("convergence_mismatch", convergence)
        print(
            "desired=",
            convergence["desired_version"],
            "(",
            convergence["desired_source"],
            ")",
            "registry=",
            convergence["registry_active_version_id"] is not None,
            "runtime=",
            convergence["runtime_active_version_id"] is not None,
        )
        print("possible:", convergence["possible_actions"], "conflict:", convergence["conflict"])
        print("=> reading the convergence view repaired nothing:")
        print(
            "   registry active version is still", get("metrics/invoice_amount/active")["version"]
        )

        # ================================================= 9A: the rollback regression
        print("\n--- 9A  Target already met != Conflict (the Phase 8 bug) ------")
        # The rollback is requested first, so the review records from=v2 to=v1.
        rollback = post(
            f"production-deployments/{deployment_id}/rollback",
            {"target_version": "1.0.0", "reason": "Synthetic convergence drill"},
            role="deployer",
        )
        save("rollback_request", rollback)
        print(
            "rollback requested:",
            rollback["from_version"],
            "->",
            rollback["to_version"],
            "preflight=",
            rollback["preflight"]["status"],
            "missing=",
            rollback["preflight"]["missing_evidence"],
        )

        # A human reconciliation adopts the runtime into the registry - "the runtime
        # is the fact". The pointer now already sits on the rollback target.
        print("\nbefore the rollback is approved, a human adopts the runtime:")
        plan = post(f"production-deployments/{deployment_id}/reconciliation-plans", role="admin")
        save("reconciliation_plan", plan)
        print(
            "plan verdict=",
            plan["verdict"],
            "possible=",
            plan["possible_actions"],
            "recommended=",
            plan["recommended_action"],
        )
        # A plan authorises nothing by itself: executing it before a human has
        # looked at it is refused outright, not silently performed.
        unreviewed = client.post(
            f"/api/v1/reconciliation-plans/{plan['reconciliation_plan_id']}/execute",
            headers=HEADERS["admin"],
        )
        print(
            "executing it before anyone reviewed it ->",
            unreviewed.status_code,
            unreviewed.json()["error"]["code"],
        )
        assert unreviewed.status_code == 409

        reconciliation_review = post(
            f"reconciliation-plans/{plan['reconciliation_plan_id']}/review",
            {"comment": "Adopt the version the runtime actually reports"},
            role="admin",
        )
        adopted = post(
            f"reconciliation-reviews/{reconciliation_review['reconciliation_review_id']}/decision",
            {
                # The human picks from what the plan actually offered.
                "decision": DECISION_FOR_ACTION[plan["recommended_action"]],
                "comment": "runtime is the fact",
            },
            role="admin",
        )
        save("reconciliation_result", adopted)
        print(
            "executed:",
            adopted["execution_status"],
            "convergence:",
            adopted["convergence_status"],
            "verdict:",
            adopted["convergence_verdict"],
        )
        print(
            "registry pointer is now",
            get("metrics/invoice_amount/active")["version"],
            "before the rollback ran",
        )

        approved = post(
            f"production-rollback-reviews/{rollback['rollback_review_id']}/decision",
            {"decision": "approved", "comment": "Execute the rollback drill"},
            role="rollback",
        )
        save("rollback_already_satisfied", approved)
        print(
            "\nrollback status=",
            approved["production_rollback_status"],
            "registry=",
            approved["registry_reconciliation_status"],
            "runtime_verified=",
            approved["runtime_verified"],
        )
        print("no_action_required=", approved["no_action_required"])
        print(
            "conflict:",
            approved["conflict_diagnosis"]["category"],
            "requires_manual_review=",
            approved["conflict_diagnosis"]["requires_manual_review"],
        )
        assert approved["registry_reconciliation_status"] == "already_satisfied"
        assert approved["no_action_required"] is True
        assert approved["conflict_diagnosis"]["category"] == "target_already_satisfied"
        print("=> Phase 8 reported a conflict here. Nothing is broken: the goal was met.")

        # ==================================== 9A: desired state, and idempotency
        print("\n--- 9A  Desired state, and converging twice -------------------")
        after = get(f"production-deployments/{deployment_id}/convergence")
        save("convergence_converged", after)
        print(
            "desired=",
            after["desired_version"],
            "source=",
            after["desired_source"],
            "state=",
            after["convergence_state"],
        )
        print(
            "target_already_satisfied=",
            after["target_already_satisfied"],
            "possible=",
            after["possible_actions"],
            "recommended=",
            after["recommended_action"],
            "auto=",
            after["automatic_correction"],
        )
        assert after["desired_source"] == "rollback"
        assert after["possible_actions"] == ["no_op"]
        assert after["automatic_correction"] is False

        # The same approved plan, run a second time, is a no-op by construction.
        again = post(f"reconciliation-plans/{plan['reconciliation_plan_id']}/execute", role="admin")
        save("reconciliation_execute_idempotent", again)
        print(
            "\nre-executing the approved plan ->",
            again["execution_status"],
            "/",
            again["final_status"],
            "/",
            again["skipped_reason"],
        )
        assert again["execution_status"] == "skipped"
        assert again["final_status"] == "already_converged"
        assert again["convergence_action"] == "no_op"

        # Nothing is left to converge, so no plan is offered at all. A plan exists
        # only when there is something a human could actually decide to do.
        nothing = client.post(
            f"/api/v1/production-deployments/{deployment_id}/reconciliation-plans",
            headers=HEADERS["admin"],
        )
        print(
            "asking for a new plan once converged ->",
            nothing.status_code,
            nothing.json()["error"]["code"],
            "- there is nothing to converge",
        )
        assert nothing.status_code == 409

        # ==================================== 9C: the gate runs before the provider
        print("\n--- 9C  Verification gate: before the action, not after -------")
        for action in ("deploy", "rollback", "reconciliation"):
            decision = get(
                f"production-deployments/{deployment_id}/verification-gate?action={action}"
            )
            save(f"verification_gate_{action}", decision)
            print(
                f"{action:>14} -> mode={decision['mode']} result={decision['result']}",
                f"missing={decision['missing_requirements']}",
            )
        print("=> the gate is action-aware, so an emergency rollback is not blocked by the")
        print("   pipeline it is trying to rescue. On a synthetic environment it records and")
        print("   does not block, because enforcing there would only teach people to bypass it.")
        anonymous = client.get(
            f"/api/v1/production-deployments/{deployment_id}/verification-gate?action=deploy"
        )
        print("anonymous gate read ->", anonymous.status_code, "- it re-reads evidence")
        assert anonymous.status_code == 401

        # ================================================ 9E: what is still open
        print("\n--- 9E  Closure matrix: which rows are actually green ---------")
        # The notification row is the one row an operator can close by configuration
        # alone, so both sides of it are shown instead of asserting a single state.
        app.state.notification_sink = None
        no_sink = get(f"production-environments/{ENVIRONMENT_ID}/closure-matrix")
        no_sink_rows = {item["name"]: item["status"] for item in no_sink["entries"]}
        print("no sink configured       -> notification_sink =", no_sink_rows["notification_sink"])
        app.state.notification_sink = MockNotificationSink()
        closure = get(f"production-environments/{ENVIRONMENT_ID}/closure-matrix")
        save("closure_matrix", closure)
        closure_rows = {item["name"]: item["status"] for item in closure["entries"]}
        print("with a sink configured   -> notification_sink =", closure_rows["notification_sink"])
        for entry in closure["entries"]:
            print(f"  {entry['name']:>28}  {entry['status']}")
        print(
            "verified=",
            closure["verified_count"],
            "not_verified=",
            closure["not_verified_count"],
            "not_applicable=",
            closure["not_applicable_count"],
            "blocked=",
            closure["blocked_count"],
        )
        print("production_closed=", closure["production_closed"])
        print("open:", closure["open_items"])
        assert closure["production_closed"] is False
        print("=> shipping Phase 9 closed nothing. Every open row is named, and")
        print("   'not_applicable' is reported rather than counted as evidence.")

        events = get("metrics/invoice_amount/events")
        save("events", events)
        phase9_events = [
            item["event_type"]
            for item in events
            if item["event_type"]
            in {
                "desired_runtime_state_established",
                "expectation_evaluated",
                "telemetry_missing_detected",
                "notification_attempted",
                "verification_gate_passed",
                "reconciliation_executed",
                "reconciliation_verified",
                "convergence_already_satisfied",
                "convergence_conflict_diagnosed",
                "production_rolled_back",
            }
        ]
        print("\nPhase 9 audit events (in order):")
        for name in phase9_events:
            print("  ", name)
        print("\nArtifacts written to", OUTPUT)
        print("Reminder: nothing in this run verified a real production runtime, and")
        print("          no timer, queue or auto-repair was created anywhere in it.")


if __name__ == "__main__":
    main()

"""Offline fixtures only. Human decisions below are explicitly simulated demo actions."""

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


def main(mode="stable"):
    """Three synthetic series: stable, OOT direction flip, and OOT degradation without a flip."""
    kind = {
        "stable": "stable",
        "direction_flip": "unstable",
        "oot_degradation": "oot-degradation",
    }[mode]
    stable = mode == "stable"
    output = Path("examples/phase5") / kind
    settings, experiments = baseline_demo(
        output_dir=str(output / "baseline"),
        database_file=f"phase5_{kind}.db",
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

        def save(name, result):
            (output / f"{name}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
                {"reviewer": "synthetic-demo", "comment": "Simulated fixture SQL review only"},
            )

        reflection = post(
            "reflections", {"experiment_run_ids": [experiments[0]["run"]["experiment_run_id"]]}
        )
        rid = reflection["run"]["reflection_run_id"]
        proposal = client.get(f"/api/v1/reflections/{rid}/proposals").json()[0]
        post(
            f"reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "accepted_for_investigation",
                "reviewer": "synthetic-demo",
                "comment": "Fixture research",
            },
        )
        refinement = post(
            "refinements",
            {
                "reflection_run_id": rid,
                "proposal_id": proposal["proposal_id"],
                "parameter_selection": {"window_days": 60},
            },
        )
        approve(refinement["candidate"])
        refinement_id = refinement["run"]["refinement_run_id"]
        result = post(f"refinements/{refinement_id}/evaluate")
        assert result["run"]["status"] == "completed", result
        result = post(
            f"refinements/{refinement_id}/decision",
            {
                "decision": "accept_candidate_for_further_validation",
                "reviewer": "synthetic-demo",
                "comment": "Investigate across synthetic dates, not promotion",
            },
        )
        save("refinement", result)
        invoices, data, periods = temporal_fixture(mode=mode)
        app.state.query_executor.fixture_rows.extend(invoices)
        app.state.query_executor.dataset_rows.extend(data)
        slices = []
        for index, (anchor, rows) in enumerate(periods):
            dataset = post(
                "datasets",
                {
                    "name": f"temporal_{index}",
                    "source": {"database": "tmp_db", "table": "invoice_risk_sample"},
                    "snapshot_time": anchor.isoformat(),
                    "partition": {"field": "dt", "value": anchor.strftime("%Y%m%d")},
                    "row_count": len(rows),
                    "checksum": dataset_checksum(rows),
                },
            )
            slices.append(
                {
                    "time_slice_id": f"slice-{index}",
                    "name": anchor.date().isoformat(),
                    "dataset_snapshot_id": dataset["dataset_snapshot_id"],
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
                "name": f"{kind} synthetic series",
                "slices": slices,
                "label_definition_id": experiments[0]["run"]["label_definition_id"],
                "reference_slice_id": "slice-0",
            },
        )
        save("series", series)
        pending = post(
            "temporal-validations",
            {"source_refinement_run_id": refinement_id, "series_id": series["series_id"]},
        )
        save("plan", pending)
        run_id = pending["spec"]["temporal_validation_run_id"]
        assert client.post(f"/api/v1/temporal-validations/{run_id}/run").status_code == 409
        for artifact in pending["spec"]["slice_artifacts"]:
            approve(artifact)
        report = post(f"temporal-validations/{run_id}/run")
        assert report["failure_category"] is None, report
        save("temporal_report", report)
        if stable:
            assert report["promotion_eligibility"] == "eligible_for_review", report["diagnostics"]
            review = post("promotion-reviews", {"temporal_validation_run_id": run_id})
            save("promotion_pending", review)
            review = post(
                f"promotion-reviews/{review['promotion_review_id']}/decision",
                {
                    "decision": "approved_for_versioning",
                    "reviewer": "synthetic-demo",
                    "comment": "Synthetic governance demonstration; no production use",
                },
            )
            save("promotion_decision", review)
        else:
            assert report["promotion_eligibility"] != "eligible_for_review", report["diagnostics"]
            assert (
                client.post(
                    "/api/v1/promotion-reviews", json={"temporal_validation_run_id": run_id}
                ).status_code
                == 409
            )
        print(kind, report["status"], report["promotion_eligibility"])
        if not stable:
            print("  diagnostics:", sorted({d["type"] for d in report["diagnostics"]}))
        for item in report["historical"] + [report["oot"]]:
            print(
                " ",
                item["name"],
                item["role"],
                item["comparison"]["outcome"],
                "coverage",
                item["baseline"]["coverage"],
                "->",
                item["candidate"]["coverage"],
                "ks",
                item["baseline"]["ks"],
                "->",
                item["candidate"]["ks"],
                "psi",
                item["candidate"]["psi"]["value"],
                item["candidate"]["direction"],
            )


if __name__ == "__main__":
    main("stable")
    main("direction_flip")
    main("oot_degradation")

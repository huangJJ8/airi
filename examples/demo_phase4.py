"""Human gates simulated explicitly for synthetic fixtures only; never production approval."""

import json
from pathlib import Path

from demo_phase3 import main as baseline_demo
from fastapi.testclient import TestClient

from airi.infrastructure.query_executor import MockQueryExecutor
from airi.main import create_app
from airi.refinement.fixtures import MockWindowReflectionLLM, refinement_fixture


def main():
    settings, experiments = baseline_demo(
        output_dir="examples/phase4/baseline",
        database_file="phase4_demo.db",
        fixture_factory=refinement_fixture,
        names=("invoice_amount_30d",),
    )
    source, labels = refinement_fixture()
    output = Path("examples/phase4")
    app = create_app(
        settings,
        reflection_llm=MockWindowReflectionLLM(),
        query_executor=MockQueryExecutor(source, dataset_rows=labels),
    )
    with TestClient(app) as client:

        def post(path, payload=None):
            response = client.post("/api/v1/" + path, json=payload)
            response.raise_for_status()
            return response.json()

        def save(name, value):
            (output / f"{name}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        reflection = post(
            "reflections", {"experiment_run_ids": [experiments[0]["run"]["experiment_run_id"]]}
        )
        rid = reflection["run"]["reflection_run_id"]
        proposals = client.get(f"/api/v1/reflections/{rid}/proposals").json()
        proposal = next(p for p in proposals if p["automation_capability"] == "supported")
        save("reflection", reflection)
        save("proposals", proposals)
        decision = post(
            f"reflections/{rid}/proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "accepted_for_investigation",
                "reviewer": "synthetic-demo",
                "comment": "Fixture research only",
            },
        )
        save("proposal_decision", decision)
        for days in (60, 90):
            request = {
                "reflection_run_id": rid,
                "proposal_id": proposal["proposal_id"],
                "parameter_selection": {"window_days": days},
            }
            generated = post("refinements", request)
            candidate, run_id = generated["candidate"], generated["run"]["refinement_run_id"]
            artifact = client.get(
                f"/api/v1/development/artifacts/{candidate['artifact_id']}"
            ).json()
            (output / f"invoice_amount_{days}d.sql").write_text(artifact["code"], encoding="utf-8")
            save(f"{days}d_request", request)
            save(f"{days}d_candidate", generated)
            # Accepting the proposal did not authorize this candidate: separate SQL review.
            assert client.post(f"/api/v1/refinements/{run_id}/evaluate").status_code == 409
            approval = post(
                "approvals",
                {
                    "artifact_id": candidate["artifact_id"],
                    "metric_ir_hash": candidate["metric_ir_hash"],
                    "artifact_hash": candidate["content_hash"],
                },
            )
            post(
                f"approvals/{approval['approval_id']}/approve",
                {
                    "reviewer": "synthetic-sql-reviewer",
                    "comment": "Reviewed this generated SQL for fixture testing only",
                },
            )
            report = post(f"refinements/{run_id}/evaluate")
            assert report["run"]["status"] == "completed", report
            save(f"{days}d_report", report)
            decision = post(
                f"refinements/{run_id}/decision",
                {
                    "decision": "need_more_evidence",
                    "reviewer": "synthetic-demo",
                    "comment": "Synthetic evidence cannot authorize promotion",
                },
            )
            save(f"{days}d_decision", decision)
            print(
                days,
                report["run"]["outcome"],
                report["comparison"]["coverage_delta"],
                report["comparison"]["ks_delta"],
                report["comparison"]["iv_delta"],
            )


if __name__ == "__main__":
    main()

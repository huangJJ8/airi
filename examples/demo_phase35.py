"""Four explicit Mock reflections of actual, newly persisted Phase 3 experiments."""

import json
from pathlib import Path

from demo_phase3 import main as experiment_demo
from fastapi.testclient import TestClient

from airi.main import create_app
from airi.reflection.llm import MockReflectionLLM


def main():
    settings, experiments = experiment_demo(
        output_dir="examples/phase35/experiments", database_file="phase35_demo.db", shared=True
    )
    output = Path("examples/phase35")
    with TestClient(create_app(settings, reflection_llm=MockReflectionLLM())) as client:
        requests = [
            (
                e["evaluation"]["metric_name"],
                {"mode": "single_metric", "experiment_run_ids": [e["run"]["experiment_run_id"]]},
            )
            for e in experiments
        ]
        requests.append(
            (
                "comparison",
                {
                    "mode": "comparison",
                    "experiment_run_ids": [e["run"]["experiment_run_id"] for e in experiments],
                },
            )
        )
        for name, request in requests:
            response = client.post("/api/v1/reflections", json=request)
            response.raise_for_status()
            report = response.json()
            assert report["llm_reflection_status"] == "completed", report
            assert report["llm_output_kind"] == "mock"
            for suffix, value in (("request", request), ("reflection", report)):
                (output / f"{name}_{suffix}.json").write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            print(
                name,
                report["run"]["status"],
                len(report["diagnostics"]),
                len(report["refinement_proposals"]),
                "Mock / synthetic",
            )


if __name__ == "__main__":
    main()

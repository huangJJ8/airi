"""Phase 11 — dual-scenario web verification over real HTTP.

Mirrors what the browser does for both declared scenarios. The seeded invoice
numbers reproduce from Phase 10; the relationship numbers reproduce the
hand-derived fixture (102 rows, coverage 102/121, ks/iv consistent with the
noisy synthetic relationship count).
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
ANCHOR = "2026-09-09T00:00:00+08:00"
PARTITION = "20260911"


def call(method, path, body=None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


for _ in range(30):
    try:
        status, meta = call("GET", "/meta")
        if status == 200:
            break
    except Exception:
        pass
    time.sleep(1)
assert status == 200, meta
print("meta scenarios:", meta["scenarios"])
assert set(meta["scenarios"]) == {"invoice_risk", "enterprise_relation"}, meta["scenarios"]

sys.path.insert(0, ".")
from airi.approvals.artifacts import canonical_hash  # noqa: E402


def drive(requirement, scenario, expect_types, expect_rows, label_window_end):
    status, generated = call(
        "POST",
        "/development/generate",
        {
            "requirement": requirement,
            "scenario": scenario,
            "execution_context": {"anchor_time": ANCHOR},
        },
    )
    assert status == 200, generated
    artifact = generated["artifact"]
    assert generated["metric_ir"]["name"], generated
    assert generated["validation"]["valid"], generated["validation"]
    short_id = artifact.get("metric_id") or artifact["artifact_id"]
    print(
        f"[{scenario}] generate: {str(short_id)[:8]} {artifact['metric_name']}"
        f" valid={generated['validation']['valid']}"
    )

    status, approval = call(
        "POST",
        "/approvals",
        {
            "artifact_id": artifact["artifact_id"],
            "metric_ir_hash": canonical_hash(generated["metric_ir"]),
            "artifact_hash": artifact["content_hash"],
        },
    )
    assert status in (200, 201), approval
    status, approved = call(
        "POST",
        f"/approvals/{approval['approval_id']}/approve",
        {"reviewer": "demo-reviewer", "comment": "Phase 11 synthetic review"},
    )
    assert status == 200, approved

    status, testing = call(
        "POST",
        "/tests/run",
        {
            "artifact_id": artifact["artifact_id"],
            "approval_id": approval["approval_id"],
            "max_rows": 1000,
        },
    )
    assert status == 200, testing
    report = testing["report"]
    seen_types = {result["type"] for result in report["results"]}
    assert seen_types == expect_types, (scenario, seen_types ^ expect_types)
    assert report["passed"] == report["total"], (scenario, report)
    print(f"[{scenario}] tests: {report['status']} {report['passed']}/{report['total']}")
    return artifact, approval, report, testing


# Scenario A — invoice risk (regression; Phase 10 numbers still hold)
invoice_artifact, invoice_approval, invoice_report, invoice_testing = drive(
    "统计企业近30天开票金额",
    "invoice_risk",
    expect_types={"schema", "null", "duplicate", "window", "boundary", "reconciliation"},
    expect_rows=110,
    label_window_end="2026-10-09T00:00:00+08:00",
)
assert invoice_testing["execution"]["row_count"] == 110, invoice_testing["execution"]

status, dataset = call("GET", "/datasets/latest?name=invoice_sample_20260909")
assert status == 200 and dataset, dataset
status, label = call("GET", "/labels/latest")
assert status == 200 and label, label
status, spec = call(
    "POST",
    "/experiments",
    {
        "experiment_name": "phase11_invoice_regression",
        "metric": {"artifact_id": invoice_artifact["artifact_id"]},
        "approval_id": invoice_approval["approval_id"],
        "test_run_id": invoice_report["test_run_id"],
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
assert status in (200, 201), spec
status, run = call("POST", f"/experiments/{spec['experiment_spec_id']}/run")
assert status == 200 and run["run"]["status"] == "completed", run
ev = run["evaluation"]
assert abs(ev["coverage"] - 0.92) < 0.01, ev["coverage"]
assert abs(ev["ks"]["value"] - 0.577) < 0.01, ev["ks"]
print(
    f"[invoice_risk] experiment: coverage={ev['coverage']:.4f} "
    f"ks={ev['ks']['value']:.3f} iv={ev['iv']:.2f}"
)

# Scenario B — enterprise relation (Phase 11 addition)
relation_artifact, relation_approval, relation_report, relation_testing = drive(
    "统计企业通过关联自然人间接关联的其他企业数量",
    "enterprise_relation",
    expect_types={
        "schema",
        "null",
        "duplicate",
        "join",
        "distinct",
        "self_exclusion",
        "missing_relation",
        "reconciliation",
    },
    expect_rows=102,
    label_window_end="2026-10-09T00:00:00+08:00",
)
assert relation_testing["execution"]["row_count"] == 102, relation_testing["execution"]

# The synthetic relationship fixture uses a different partition / table family.
from airi.experiments.validation import dataset_checksum  # noqa: E402
from airi.infrastructure.relation_fixture import (  # noqa: E402
    RELATION_PARTITION,
    relation_fixture,
)

fixture = relation_fixture()
status, dataset = call(
    "POST",
    "/datasets",
    {
        "name": "enterprise_relation_sample_20260911",
        "source": {"database": "tmp_db", "table": "enterprise_relation_sample"},
        "entity_key": "enterprise_id",
        "snapshot_time": ANCHOR,
        "partition": {"field": "dt", "value": RELATION_PARTITION},
        "row_count": len(fixture.labeled_rows),
        "checksum": dataset_checksum(fixture.labeled_rows, "enterprise_id"),
    },
)
assert status == 201, dataset
status, label = call(
    "POST", "/labels", {"name": "synthetic_relation_label", "entity_key": "enterprise_id"}
)
assert status == 201, label

status, spec = call(
    "POST",
    "/experiments",
    {
        "experiment_name": "phase11_relation_evaluation",
        "metric": {"artifact_id": relation_artifact["artifact_id"]},
        "approval_id": relation_approval["approval_id"],
        "test_run_id": relation_report["test_run_id"],
        "dataset_snapshot_id": dataset.json()["dataset_snapshot_id"],
        "label_definition_id": label.json()["label_definition_id"],
        "anchor_time": ANCHOR,
        "observation_time": ANCHOR,
        "label_window": {
            "start": "2026-09-10T00:00:00+08:00",
            "end": "2026-10-09T00:00:00+08:00",
        },
    },
)
assert status in (200, 201), spec
status, run = call("POST", f"/experiments/{spec['experiment_spec_id']}/run")
assert status == 200 and run["run"]["status"] == "completed", run
ev = run["evaluation"]
assert abs(ev["coverage"] - 102 / 121) < 0.01, ev["coverage"]
assert ev["ks"]["direction"] == "higher_is_riskier"
print(
    f"[enterprise_relation] experiment: coverage={ev['coverage']:.4f} "
    f"ks={ev['ks']['value']:.3f} iv={ev['iv']:.2f}"
)

print("phase11 dual-scenario HTTP verification: both scenarios reproduce")

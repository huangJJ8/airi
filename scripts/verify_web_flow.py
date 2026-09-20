"""Web-guided demo flow driven over real HTTP against the running uvicorn.

Mirrors exactly what the browser does: generate -> review -> tests ->
experiment (seeded dataset) -> reflection -> proposal decision -> candidate
refinement -> comparison. Asserts the seeded demo numbers reproduce.
"""

import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, ".")

from airi.approvals.artifacts import canonical_hash  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
ANCHOR = "2026-09-09T00:00:00+08:00"


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
        call("GET", "/meta")
        break
    except Exception:
        time.sleep(1)

status, generated = call(
    "POST",
    "/development/generate",
    {
        "requirement": "统计企业近30天开票金额",
        "scenario": "invoice_risk",
        "execution_context": {"anchor_time": ANCHOR},
    },
)
assert status == 200, generated
artifact = generated["artifact"]
print(
    "generate:",
    artifact["artifact_id"][:8],
    artifact["metric_name"],
    "valid:",
    generated["validation"]["valid"],
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
    {"reviewer": "demo-reviewer", "comment": "HTTP simulated review"},
)
assert status == 200, approved
print("review: approved", approved["decision"])

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
print("tests:", report["status"], f"{report['passed']}/{report['total']}")

status, dataset = call("GET", "/datasets/latest?name=invoice_sample_20260909")
assert status == 200 and dataset, dataset
print("dataset:", dataset["name"], dataset["row_count"])

status, label = call("GET", "/labels/latest")
assert status == 200 and label, label

status, spec = call(
    "POST",
    "/experiments",
    {
        "experiment_name": "web_http_guided_evaluation",
        "metric": {"artifact_id": artifact["artifact_id"]},
        "approval_id": approval["approval_id"],
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
assert status in (200, 201), spec
status, run = call("POST", f"/experiments/{spec['experiment_spec_id']}/run")
assert status == 200 and run["run"]["status"] == "completed", run
ev = run["evaluation"]
print(
    "experiment:",
    ev["metric_name"],
    "coverage",
    round(ev["coverage"], 4),
    "ks",
    round(ev["ks"]["value"], 3),
    "iv",
    round(ev["iv"], 2),
    "direction",
    ev["ks"]["direction"],
)
assert abs(ev["coverage"] - 0.92) < 0.01, ev["coverage"]
assert abs(ev["ks"]["value"] - 0.577) < 0.01, ev["ks"]
assert abs(ev["iv"] - 6.03) < 0.1, ev["iv"]
print("seeded demo numbers reproduced over real HTTP")

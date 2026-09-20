# API Guide

The backend is a FastAPI application. Interactive, always-current reference:

| | |
| --- | --- |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| OpenAPI JSON | <http://localhost:8000/openapi.json> |
| Liveness | `GET /health` |
| Runtime facts | `GET /api/v1/meta` |

This page explains the **domain** endpoints — the ones that carry meaning.
There are 116 route entries in total; the full list is in the OpenAPI schema,
not reproduced here.

---

## Conventions

- Base path: `/api/v1`
- All request/response bodies are **strict** Pydantic schemas: unknown fields
  are rejected rather than ignored.
- Errors return a structured body with a machine-readable `error_code` (for
  example `requirement_parse_failed`, `artifact_hash_mismatch`). The Web UI
  renders the backend's code instead of inventing its own message.
- Governance conflicts return **409** with the reason; validation problems
  return **422**.
- Every response carries `X-Request-ID`; the server logs `request_completed`
  with the same id, method, status, and duration.

### `GET /api/v1/meta`

Read-only runtime facts used by the UI's honest-mode banner. Deliberately
exposes no domain state.

```json
{
  "app_name": "AIRI",
  "version": "1.0.0",
  "environment": "local",
  "execution_mode": "mock",
  "llm_mode": "demo_mock",
  "production_deployed": false,
  "scenarios": ["enterprise_relation", "invoice_risk"]
}
```

`scenarios` is derived from the registered scenario skills — the UI never
hardcodes the demo families.

---

## Development

`src/airi/api/development.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/development/generate` | requirement → Metric IR + generated SQL draft + validation |
| `GET` | `/api/v1/development/artifacts/{artifact_id}` | fetch a stored artefact |

```bash
curl -X POST http://localhost:8000/api/v1/development/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "requirement": "统计企业关联自然人控制的其他企业数量",
    "scenario": "enterprise_relation"
  }'
```

The response contains the parsed scenario, selected capabilities, the Metric IR,
the generated SQL, and the static validation result. **Nothing is executed and
nothing is approved here.** An unknown or ambiguous requirement fails with
`requirement_parse_failed` instead of guessing.

---

## Approvals

`src/airi/api/approvals.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/approvals` | open an approval request for an artifact |
| `POST` | `/api/v1/approvals/{approval_id}/approve` | approve |
| `POST` | `/api/v1/approvals/{approval_id}/reject` | reject |
| `GET` | `/api/v1/approvals/{approval_id}` | status |

An approval request carries the content hashes it is approving:

```json
{
  "artifact_id": "...",
  "metric_ir_hash": "...",
  "artifact_hash": "..."
}
```

If the hashes do not match the stored artefact, the request is refused. This is
what makes approval mean *"this exact content"* rather than *"this artifact id"*.

---

## Testing

`src/airi/api/tests.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/tests/run` | run the automated test suite for an approved artifact |
| `GET` | `/api/v1/tests/{test_run_id}` | fetch the test report |

The **test list is produced by the backend** from the scenario's declared
`test_types`. `invoice_risk` gets window-boundary tests; `enterprise_relation`
gets join correctness, distinct count, and self-relation exclusion. The UI
renders whatever comes back (`name`, `status`, `evidence`) and never decides
test names itself — that is why a metric with no window never displays a
"Window Semantics PASS" row.

---

## Experimentation

`src/airi/api/experiments.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/datasets` | register a dataset |
| `GET` | `/api/v1/datasets/latest` | latest dataset (`?name=` to pick a demo sample) |
| `POST` | `/api/v1/labels` | register a label definition (outcome) |
| `GET` | `/api/v1/labels/latest` | latest label definition |
| `POST` | `/api/v1/experiments` | create an experiment spec |
| `POST` | `/api/v1/experiments/{spec_id}/run` | run it |
| `GET` | `/api/v1/experiments/runs/{run_id}` | fetch the run |

The evaluation block is computed by Python (deterministic) and includes
`coverage`, `bad_rate`, `distribution`, `bins`, `ks` (with `direction`),
`iv` (with `iv_status`), and `threshold_candidates`. Both demo scenarios use
this same engine — there is no second evaluation stack.

---

## Reflection

`src/airi/api/reflections.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/reflections` | run reflection over an experiment |
| `GET` | `/api/v1/reflections/{reflection_run_id}` | fetch the reflection |
| `GET` | `/api/v1/reflections/{reflection_id}/proposals` | bounded proposals |
| `POST` | `/api/v1/reflections/{reflection_id}/proposals/{proposal_id}/decision` | accept/reject a proposal |

Reflection output is a **separate artefact type**. It cites statistics; it
cannot overwrite them, and it cannot modify an IR, a threshold, or a label
definition. Proposals are advisory and require an explicit decision.

---

## Registry

`src/airi/api/registry.py`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/metric-versions` | register an approved metric version |
| `GET` | `/api/v1/metrics` | list metric keys |
| `GET` | `/api/v1/metrics/{metric_key}` | metric summary |
| `GET` | `/api/v1/metrics/{metric_key}/active` | current active version |
| `GET` | `/api/v1/metrics/{metric_key}/versions` | version history |
| `GET` | `/api/v1/metrics/{metric_key}/versions/{version}` | one version |
| `GET` | `/api/v1/metrics/{metric_key}/versions/{from}/compare/{to}` | compare two versions |
| `GET` | `/api/v1/metrics/{metric_key}/events` | registry audit events |
| `POST` | `/api/v1/metrics/{metric_key}/rollback` | roll back to a previous version |

Versions are immutable. A change produces a new version; history is never
edited. Every state change appends an audit event.

---

## Lifecycle and production domains

These are implemented and exercised by tests, but the local demo does not drive
them, and they are **not verified** against a real environment in this
repository. Grouped here for orientation only:

| Domain | Prefix | Notes |
| --- | --- | --- |
| Promotion review | `/api/v1/promotion-reviews` | gate before temporal validation |
| Temporal validation | `/api/v1/temporal-validations`, `/api/v1/temporal-series` | OOT / stability |
| Refinement | `/api/v1/refinements` | bounded proposal evaluation |
| Release | `/api/v1/releases`, `/api/v1/release-reviews` | gated activation |
| Execution | `/api/v1/executions` | test/execution runtime |
| Spark test env | `/api/v1/environments/spark-test` | operator-verified test cluster, optional |
| Production | `/api/v1/production-*` | adapters default to inert; fail closed |
| Monitoring | `/api/v1/metric-alerts`, `/api/v1/monitoring-*`, `/api/v1/telemetry-sources` | snapshot / expectation based |

Two invariants are worth knowing when reading that group:

- Identity providers and production adapters **default to disabled**. A
  misconfigured production deployment fails closed rather than trusting a
  client-supplied name or a synthetic runtime.
- "Deployment returned success" is not "runtime verified" — verification,
  reconciliation, and convergence are separate recorded artefacts.

---

## Next

- [Quickstart](quickstart.md) — run the server
- [Demo Guide](demo.md) — the workflow these endpoints drive
- [Design Principles](../architecture/design-principles.md) — why the boundaries are here

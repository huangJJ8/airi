# Real Environment Checklist — Phase 8

Phase 8 can only be *completed* by an operator standing in front of a real runtime.
Everything in this repository is a **seam plus an inequality guard**: the code
refuses to claim a fact it did not observe, and this checklist is how a human
turns "NOT VERIFIED" into "VERIFIED" — or leaves it honestly unverified.

> The test suite never manufactures a green result for this. Integration tests
> marked `production_integration`, `identity_integration`, `telemetry_integration`
> **skip** unless the environment below is configured. A skip is the correct
> outcome when the checklist has not been executed.

---

## 0. The inequalities you are verifying

Phase 8 exists because these seven pairs are *not* the same claim. Each section
below ends with the observation that separates the left from the right.

| # | Left (cheap, misleading) | Right (what Phase 8 requires) |
|---|---|---|
| 1 | Configured | Verified |
| 2 | Connected | Authenticated |
| 3 | Authenticated | Authorized |
| 4 | Deploy returned success | Runtime verified |
| 5 | Monitoring received | Telemetry trusted |
| 6 | Mismatch detected | Mismatch fixed |
| 7 | Recovery executed | Convergence verified |

---

## 1. Runtime access (Phase 8A)

### Prerequisites

- [ ] A reachable Spark Thrift Server (or compatible) you are allowed to probe.
- [ ] A service account that is read-only on the production catalog.
- [ ] Network path from the AIRI host to the Thrift port.
- [ ] Agreement on which cluster identifier string identifies this runtime
      (it becomes part of the fingerprint; it must not be a human nickname that
      someone can change without a real change happening).

### Configuration

```
AIRI_PRODUCTION_ADAPTER=spark_production
AIRI_SPARK_PRODUCTION_HOST=<thrift-host>
AIRI_SPARK_PRODUCTION_PORT=10000
AIRI_SPARK_PRODUCTION_USERNAME=<service-account>
AIRI_SPARK_PRODUCTION_DATABASE=c_db
AIRI_SPARK_PRODUCTION_READ_ONLY_ATTESTED=true
AIRI_SPARK_PRODUCTION_AUTH_MODE=NOSASL        # LDAP | KERBEROS | GATEWAY
AIRI_SPARK_PRODUCTION_PASSWORD=<secret>      # required when AUTH_MODE=LDAP
AIRI_SPARK_PRODUCTION_SESSION_TIMEZONE=Asia/Shanghai
AIRI_SPARK_PRODUCTION_CATALOG=<catalog-name>
AIRI_SPARK_PRODUCTION_CLUSTER_IDENTIFIER=<stable-cluster-id>
AIRI_SPARK_PRODUCTION_ACTIVATION_LEDGER=     # optional control channel, see §4
```

`AIRI_SPARK_PRODUCTION_READ_ONLY_ATTESTED` is a **human attestation**, not a
detected capability. Setting it to `true` without an actual read-only grant is
the single easiest way to make this checklist lie. Verify the grant first.

### Separation of concerns

- [ ] `SparkProductionAdapter.configured` is true (host + username present).
- [ ] `adapter.authoritative` is true and `adapter.runtime_mode == "spark_production"`.
- [ ] A probe returns `reachable=True`.
- [ ] `probe.fingerprint(...)`.fingerprint_hash is 64 hex chars and **stable across
      two independent probes** — re-probe and compare. A hash that changes
      between probes means the fingerprint is reading something volatile.

**Inequality 1 (Configured ≠ Verified):** a filled-in `.env` proves nothing.
Run the probe; only `reachable=True` with a stable hash counts.

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m production_integration -q
```

---

## 2. Credentials (inequality 2)

- [ ] `AUTH_MODE=NOSASL` → the session is **connection-only**. `probe.authenticated`
      must stay `False` and `probe.runtime_identity_verified` must stay `False`.
      Do not "fix" this by asserting the username proves identity.
- [ ] `AUTH_MODE=LDAP` with a password → `probe.authenticated` must be `True`
      **and** a runtime identity must be observed, not constructed.
- [ ] Confirm on the server side (audit log / session list) that a session from
      this host actually appears. A client that thinks it connected is not evidence.

**Inequality 2 (Connected ≠ Authenticated):** open a NOSASL session and confirm
the adapter reports `authenticated=False`. If it reports `True`, that is a bug.

---

## 3. Trusted identity (Phase 8B)

### Prerequisites

- [ ] An API gateway in front of AIRI that **strips** any inbound
      `x-airi-actor` / attestation header from untrusted clients.
- [ ] A shared secret between gateway and AIRI, or a JWT signing key.

### Configuration (gateway attestation)

```
AIRI_PRODUCTION_IDENTITY_PROVIDER=trusted_header
AIRI_PRODUCTION_IDENTITY_HEADER=x-airi-actor
AIRI_PRODUCTION_IDENTITY_TRUST_BOUNDARY=trusted_gateway
AIRI_PRODUCTION_IDENTITY_GATEWAY_HEADER=x-airi-gateway-attestation
AIRI_PRODUCTION_IDENTITY_GATEWAY_SECRET=<shared-secret>
```

### Configuration (signed token)

```
AIRI_PRODUCTION_IDENTITY_PROVIDER=trusted_header
AIRI_PRODUCTION_IDENTITY_TRUST_BOUNDARY=signed_jwt
AIRI_PRODUCTION_IDENTITY_JWT_SECRET=<hs256-key>
AIRI_PRODUCTION_IDENTITY_ISSUER=<expected-iss>
```

### Verification

- [ ] With the gateway in place, a request **with** the attestation header is
      accepted and the review records `reviewer_auth_source`.
- [ ] A request sent **bypassing** the gateway (direct to the AIRI port) with a
      hand-written `x-airi-actor` header is rejected: `identity_not_trusted`.
      *This is the actual test of the boundary.* If it succeeds, the boundary is
      not deployed — remove the header at the edge.
- [ ] With the boundary configured and a deployment live, a local/mock identity
      is refused rather than silently accepted.

**Inequality 3 (Authenticated ≠ Authorized):** a trusted identity still has to be
authorized for the specific action. Confirm that a trusted-but-wrong-role actor
is refused by the normal authorization path, not granted by the identity path.

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m identity_integration -q
```

---

## 4. Deployment + provider confirmation (Phase 8C — inequality 4)

### Configuration

Deployment control needs the optional control channel:

```
AIRI_SPARK_PRODUCTION_ACTIVATION_LEDGER=<ledger-table-or-endpoint>
```

Without it, connectivity and shadow are verifiable while deployment control
stays NOT VERIFIED — which is the honest state, not a failure.

### Verification

- [ ] A preflight run lists `activation_control_channel` as `passed` only when the
      ledger is configured.
- [ ] Trigger a deployment. The response returning success is **not** the
      evidence. Confirm a `ProductionDeploymentEvidence` row exists and that it
      came from a **second, independent status read** (`provider_confirmed`).
- [ ] Deliberately test the negative: ask for a deployment the provider will
      refuse, and confirm no evidence row claims success.
- [ ] Confirm the evidence carries the provider's own job/deployment id, not an
      identifier AIRI invented.

**Inequality 4 (Deploy returned success ≠ Runtime verified):** if the second
status read is unavailable, the deployment must remain unverified rather than
inheriting the first response's optimism.

---

## 5. Trusted telemetry (Phase 8C — inequality 5)

### Prerequisites

- [ ] A monitoring producer with its own credentials.
- [ ] An agreement on `source_event_id` — it must be unique per observation and
      stable across retries.

### Registration (this is not verification)

```
POST /api/v1/telemetry-sources
{
  "telemetry_source_id": "prod_monitor",
  "source_system": "prod_monitor",
  "environment_id": "prod_real",
  "auth_identity": "monitor@example.invalid",
  "registered_by": "<operator>"
}
```

- [ ] The created source reports `verified: false`. Registration alone never
      marks a producer trusted.
- [ ] `POST /api/v1/telemetry-sources/prod_monitor/verify` with the operator's
      identity flips `verified` to `true` and records who did it.

### Ingestion checks

- [ ] Ingest a snapshot and confirm `telemetry_trust`:
      `trusted_source` (registered + verified + `synthetic=false`),
      `synthetic` (registered but synthetic), or `unverified`.
- [ ] Send the same `source_event_id` twice → exactly one snapshot row is created
      (idempotent), and the second call is not double-counted.
- [ ] Confirm `observation_time` and `received_at` are **different clocks**:
      `observation_lag_hours` = received − observed. A producer with a wrong
      clock must show up as `stale`/`late`, not silently as `fresh`.
- [ ] Do **not** configure a scheduler. `MonitoringExpectation` is a *declared
      expectation* that can report `overdue`; it does not poll and does not alert.

**Inequality 5 (Monitoring received ≠ Telemetry trusted):** confirm a snapshot
from an unregistered source is refused (404), and a snapshot from a registered
but unverified source is stored with `telemetry_trust="unverified"` and never
counted as trusted.

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m telemetry_integration -q
```

---

## 6. Verification report (inequality 1, again, at the deployment level)

- [ ] `POST /api/v1/production-deployments/{id}/verification` produces a report
      whose sections are individually `verified` / `not_verified` — never one
      rolled-up boolean.
- [ ] `real_environment_verified` is `true` only when every required section is
      verified.
- [ ] Swap the runtime underneath (point the fingerprint at a different cluster)
      and confirm the report flags the swap instead of passing.

---

## 7. Auditable reconciliation (Phase 8D — inequality 6)

- [ ] Produce a real mismatch (runtime active version ≠ registry version).
- [ ] `POST /api/v1/reconciliation-plans` enumerates the **available actions**
      (`registry_to_runtime`, `runtime_to_registry`, `manual_investigation`) and
      declares **none** of them true. Verify the plan text contains no claim
      about which side is correct.
- [ ] Create a review, then approve it. Approval does not execute.
- [ ] Execute and confirm the result records who approved and which action ran.
- [ ] Confirm the chosen action is one the plan actually offered — an action that
      was never offered is refused, not performed.
- [ ] If the recovery cannot execute, the plan must end with the mismatch still
      **open**, not closed.

**Inequality 6 (Mismatch detected ≠ Mismatch fixed):** the plan never declares
the truth; only a human decision plus re-observation can.

---

## 8. Rollback + convergence (Phase 8E — inequality 7)

- [ ] Run the rollback preflight and confirm it names any missing evidence
      (target, previous version, control channel) explicitly.
- [ ] Execute the rollback. Its return value is **not** the evidence.
- [ ] Re-observe the runtime and confirm `convergence_status` is confirmed by a
      fresh read (`ReconciliationResult` / rollback verification).
- [ ] Confirm `production_rollback_reviews.runtime_verified` is `true` only when
      the post-rollback read agreed.
- [ ] Deliberately point the rollback at a target it cannot reach and confirm it
      ends `not_verified` / `failed` with a failure category — never a
      manufactured success.

**Inequality 7 (Recovery executed ≠ Convergence verified):** the only acceptable
evidence of convergence is a new observation taken after the recovery.

---

## 9. Policy

```
# Default. A rollback is NOT required for a deployment to count as verified.
AIRI_PRODUCTION_REQUIRE_ROLLBACK_VERIFIED=false
```

- [ ] Leave the default unless your organization explicitly requires a verified
      rollback for every deployment. Phase 8 must never *manufacture* a rollback
      just to satisfy a policy — if the policy requires it and none happened, the
      report stays `not_verified`.

---

## 10. Final sign-off

- [ ] All integration tests run with `AIRI_ENVIRONMENT=production` and **do not
      skip**.
- [ ] Every item above is either checked, or explicitly recorded as unverified
      with a reason.
- [ ] `docs/history/phase8-report.md` §real-environment section is updated with what was
      actually observed.
- [ ] No item in this checklist was satisfied by a mock, a fixture, or an
      assumption.

**A checklist that cannot be completed in your environment is a valid outcome.**
Record the gap; do not close it with a simulation.

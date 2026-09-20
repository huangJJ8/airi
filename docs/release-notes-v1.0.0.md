# AIRI v1.0.0 — Portfolio Edition

**An AI-assisted risk metric research & development platform.**

AIRI turns a natural-language risk requirement into a strictly validated
**Metric IR**, deterministic SQL, automated metric tests, statistical
experiments, evidence-driven reflection, and governed metric versions.

> **LLMs reason. Python verifies. Humans govern.**

This is the first public release. AIRI v1.0.0 is **feature-complete by design** —
the platform is frozen, and future capability work lives in the
[Roadmap](../README.md#roadmap) rather than in the release.

---

## Highlights

- **Natural-language requirement parsing** — a risk requirement compiles into a
  strict, versioned Pydantic contract; ambiguous or cross-domain requirements are
  rejected, never guessed.
- **Structured Metric IR** — the reviewable boundary between the model and the
  database. The model may propose; only the schema admits.
- **Scenario Skill × Capability Skill** — domain semantics separated from
  execution mechanics. Capabilities are declared once and pin tool versions.
- **Deterministic SQL generation** — SQL is emitted by versioned Python tools and
  controlled Jinja templates. **The model never writes the SQL that ships.**
- **Static SQL validation** — a closed grammar (full-match, not a substring
  blacklist) with DDL/DML refused outright.
- **Automated metric testing** — schema, null handling, duplicates, window
  boundaries, join correctness, distinct semantics, self-loop exclusion,
  reconciliation, and a direct-join control for joined metrics.
- **KS / IV / Lift experimentation** — coverage, bad rate, decile bins, KS with
  direction, IV, lift, threshold candidates, and PSI against a frozen reference.
- **Evidence-driven LLM reflection** — hypotheses are stored separately from
  computed facts, in a different type and a different table.
- **Controlled refinement** — bounded, human-decided proposals. Nothing is
  silently replaced.
- **Temporal validation** — multi-slice historical and out-of-time slicing with
  PSI-based stability diagnostics.
- **Metric Registry** — immutable, content-hashed versions with an audit-event
  stream and two-phase rollback.
- **Human-in-the-loop governance** — four distinct recorded gates: SQL approval,
  promotion review, release review, deployment review.
- **Vue Web UI** — dashboard, development, testing, experiment, reflection,
  registry. Zero business computation in the browser; the browser never calls an
  LLM.
- **Two demo scenarios through one identical workflow** — Invoice Risk and
  Enterprise Relation.

---

## Architecture

```text
Natural Language
  → Metric IR               strict Pydantic schema, versioned
  → Skill Planner           scenario skill × capability skills
  → Tool Planner            deterministic Python tool, pinned version
  → SQL artifact            Jinja template, content-addressed
  → Static validation       closed allow-list grammar
  → Automated testing       scenario-declared probe checks
  → Experiment              coverage / KS / IV / lift / PSI
  → Reflection              hypotheses (never auto-applied)
  → Registry + Governance   immutable versions, human approval gates
```

Three layers own three different things, and the boundary is the design:

| Layer | Owns |
| --- | --- |
| **LLM** | Requirement understanding, evidence interpretation |
| **Python** | IR validation, SQL generation, testing, statistics, hashing |
| **Human** | Approval, promotion, release, deployment |

Full detail:
[Architecture Overview](https://github.com/OWNER/REPO/blob/main/docs/architecture/overview.md)
·
[Design Principles](https://github.com/OWNER/REPO/blob/main/docs/architecture/design-principles.md)

---

## Demo Scenarios

**Invoice Risk** — "统计企业近30天开票金额"

```text
invoice_risk × metric_sum × metric_window
→ single-source windowed SUM
```

**Enterprise Relation** — "统计企业关联自然人控制的其他企业数量"

```text
enterprise_relation × metric_join × metric_count
→ two-hop JOIN (enterprise → person → enterprise)
→ COUNT DISTINCT with self-loop exclusion
```

Both scenarios share the same parser, IR, planners, SQL generator, validator,
approval gate, testing layer, experiment layer and web workflow. The second
scenario required **no new orchestration** — the join mechanism was promoted to a
general capability (`metric_join@1.0.0`) rather than written as a scenario
special case.

> `New Scenario ≠ New Workflow.`

---

## Web UI

Vue 3 + TypeScript + Vite + Element Plus + ECharts, six pages:
Dashboard · Metric Development · Metric Testing · Experiment ·
Reflection & Refinement · Metric Registry.

Every KS / IV / lift value and every version state comes from the backend API.
The frontend performs **zero** business computation and renders governance
conflicts (HTTP 409) using the backend's error code rather than inventing a
message.

---

## Testing

| Suite | Result |
| --- | --- |
| Backend (`pytest`) | **675 passed / 14 skipped** |
| Backend coverage | **91%** |
| Frontend (`vitest`) | **28 passed** |
| Lint (`ruff check` + `format --check`) | clean |
| Migrations (`alembic check`) | head `0011_operational_convergence`, no drift |
| Open-source safety scan | clean |

The 14 skipped tests are the integration suites that require real infrastructure
(Spark/Hive, MySQL, production identity, telemetry). They are **skipped, not
mocked** — a green suite never implies a verified integration.

---

## Run Locally

Requirements: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), Node.js ≥ 20.19.

**Windows (verified):**

```powershell
git clone <YOUR_REPO_URL>
cd airi
.\scripts\start-demo.ps1     # migrate → seed synthetic data → start both servers
```

Then open <http://localhost:5173>. API docs at <http://localhost:8000/docs>.
Stop with `.\scripts\stop-demo.ps1`.

**macOS / Linux (best-effort)** — `./scripts/start-demo.sh` / `./scripts/stop-demo.sh`.
The mirror scripts exist but the verified environment for this project is Windows.

**No install, terminal only:**

```bash
uv run --frozen python examples/quick_demo.py
```

**Docker** — `docker compose up --build`. Provided and statically reviewed;
runtime **NOT VERIFIED** (Docker was unavailable in the development
environment).

No API key, no cluster, no internal network required. The demo runs on SQLite
with a deterministic LLM substitute (`AIRI_LLM_MODE=demo_mock`) — a deliberate,
explicitly configured substitute, not a fallback.

---

## Known Limitations

Stated plainly, because they matter:

- **All bundled data is synthetic.** Every dataset is generated in-repo. The
  reported statistics demonstrate the pipeline; they say **nothing** about real
  predictive power.
- **Spark / Hive / MySQL production integrations are not verified.** The
  adapters and executors exist in code and are exercised at the boundary layer,
  but they have never been run against a real environment in this repository.
  They default to inert and fail closed.
- **This is a portfolio / research implementation.** It must **not** be
  interpreted as validated financial-risk infrastructure.
- **`production_deployed` is `false` everywhere by construction.**
- **Reviewer identity is caller-asserted** in demo mode (demo-grade, no
  authentication).
- **Docker runtime is unverified**, and the `.sh` scripts are best-effort.
- **One synthetic identifier is intentionally opaque** — an internal-looking
  table name kept to avoid invalidating historical artifacts. It is a placeholder
  with no real data behind it.

See [Limitations](https://github.com/OWNER/REPO/blob/main/README.md#limitations)
and the [Real Environment Checklist](https://github.com/OWNER/REPO/blob/main/docs/guides/real-environment-checklist.md).

---

**Full Changelog**: [CHANGELOG.md](https://github.com/OWNER/REPO/blob/main/CHANGELOG.md)
**License**: Apache-2.0

# Changelog

All notable changes to AIRI are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

## v1.0.0 — Portfolio Edition

First public release. AIRI is an AI-assisted risk metric research and
development platform: natural-language requirements become structured
Metric IR, deterministic SQL, automated tests, experiments, reflection and
governed metric versions. **LLMs reason. Python verifies. Humans govern.**

### Core platform

- **Requirement understanding**: natural-language risk requirements parsed
  into strictly validated Pydantic Metric IR (atomic + derived metrics,
  optional Join IR for multi-source metrics). Ambiguous or cross-domain
  requirements are rejected, never guessed.
- **Scenario Skill × Capability Skill architecture**: domain knowledge
  (entities, data sources, business rules) lives in scenario skills;
  execution mechanics (aggregation, windows, joins, SQL generation) live in
  reusable, versioned capability skills. Adding a scenario adds knowledge,
  not a new workflow.
- **Deterministic SQL generation**: SQL is produced by versioned Python tools
  and Jinja templates from the Metric IR - the LLM never writes final SQL.
  Every artifact is validated by a strict static SQL validator (allowlisted
  sources, fields, join types; no subqueries, comments, DDL or DML).
- **Human-in-the-loop governance**: SQL approval gates execution; metric
  versions, releases, promotions, deployments and rollbacks each have their
  own independent review. Reviewer identity is caller-asserted (demo-grade,
  no authentication).
- **Automated metric testing**: deterministic probe-based tests with an
  independent Decimal reference implementation (schema, null handling,
  duplicates, window semantics, join correctness, distinct semantics,
  self-relation exclusion, reconciliation).
- **Metric experimentation**: KS / IV / Lift / Coverage / bad rate with
  threshold candidates and risk direction, on registered dataset snapshots
  and label definitions. All statistics are deterministic Python.
- **Evidence-driven LLM reflection**: reflection may only cite evidence from
  persisted experiment reports; proposals are validated against an allowlist
  and always require human review. Facts stay deterministic; only
  interpretation is probabilistic.
- **Controlled refinement**: accepted proposals produce candidate metrics
  through the same governed pipeline (new IR → new SQL → new approval → new
  tests → baseline comparison). Nothing is silently replaced.
- **Temporal validation**: multi-slice historical + out-of-time validation
  with PSI-based stability diagnostics and frozen threshold transfer.
- **Metric registry & controlled release**: immutable, content-hashed metric
  versions; staging/shadow validation; release review; CAS activation;
  two-phase rollback with full audit event history.
- **Operational reliability semantics**: desired state vs registry pointer vs
  runtime state kept distinct; convergence is observed, not assumed; alerts
  are not notifications; reconciliation detects but never auto-corrects.

### Web UI

- Vue 3 + TypeScript + Element Plus + ECharts frontend (`airi-web/`) with
  six pages: Dashboard, Metric Development, Metric Testing, Experiment,
  Reflection & Refinement, Metric Registry.
- The browser performs **zero** business computation, never calls an LLM,
  and renders governance errors (409 codes) from the backend verbatim.

### Demo scenarios

- **Invoice Risk** — "统计企业近30天开票金额"
  (`invoice_risk` × `metric_sum` × `metric_window` → single-table windowed
  SUM SQL).
- **Enterprise Relation** — "统计企业关联自然人控制的其他企业数量"
  (`enterprise_relation` × `metric_join` × `metric_count` → two-hop JOIN +
  COUNT DISTINCT with self-loop exclusion).

Both scenarios share the same parser, IR, planners, SQL generator, validator,
approval, testing, experiment and web workflow.

### Open-source packaging

- One-command local demo (`scripts/start-demo.ps1` / `start-demo.sh`) -
  SQLite + demo_mock LLM + synthetic data, no Spark / MySQL / API key needed.
- Terminal quick demo (`examples/quick_demo.py`).
- GitHub Actions CI (backend tests + ruff + alembic, frontend build + tests,
  open-source safety scan).
- Docker Compose configuration (runtime NOT VERIFIED in this repository).
- Apache License 2.0, CONTRIBUTING, SECURITY policy, open-source safety
  scanner, architecture documentation, and full engineering history
  (`docs/history/`).

### Honest limitations

- All data is synthetic; no real Spark/Hive/MySQL/identity/telemetry
  integration has been verified (integration tests skip and say why).
- `production_deployed` is `false` everywhere by construction.
- The demo LLM mode is a deterministic substitute, not model inference.

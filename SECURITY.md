# Security Policy

## Project scope

AIRI is a **research / portfolio implementation** of an AI-assisted risk
metric research and development platform. It is designed to run locally with
synthetic data. It is **not** a production financial risk system and has not
been verified against real Spark/Hive clusters, real identity providers, or
real telemetry pipelines.

## Data policy

- All bundled demo data (invoice facts, enterprise relations, labels,
  experiment samples) is **synthetic**. No real customer data, real company
  names, real persons, or real identifiers are included.
- Data-source identifiers such as `c_db.source_fp_jdc_view`,
  `tmp_db.airi_invoice_fixture` and `demo.*` are **opaque synthetic
  identifiers** used consistently across fixtures, prompts and generated SQL.
  They carry no real data and are not references to a live environment.
- Do **not** submit real financial data, real credentials, or real internal
  hostnames in issues, pull requests, fixtures, or screenshots.

## Credentials

- The default local demo needs **no** API keys: it uses SQLite, a
  deterministic `demo_mock` LLM substitute, and synthetic fixtures.
- If you configure a real LLM endpoint, put credentials in `.env` (which is
  git-ignored) - never commit them.
- The repository ships `.env.example` with placeholder values only.

## What is intentionally NOT security-hardened

- The reviewer identity on approvals is caller-asserted; there is **no
  login/authentication** on the API. This is acceptable only for controlled
  local/demo environments.
- Production adapters (`spark_test`, production provider, identity trust
  boundary, telemetry attestation) exist as **failure-closed integration
  seams**. They are NOT VERIFIED against real infrastructure in this
  repository; see `REAL_ENVIRONMENT_CHECKLIST.md` and the phase reports in
  `docs/history/`.

## Reporting a vulnerability

Please use GitHub **Security Advisories** ("Report a vulnerability" under the
Security tab) rather than opening a public issue. Include reproduction steps
and, where relevant, the affected phase/report. There is no SLA commitment -
this is a portfolio project - but reports are appreciated and will be
addressed as time permits.

## Safety scanner

`scripts/check_open_source_safety.py` scans the repository for
high-confidence sensitive patterns (private keys, credential-shaped tokens,
forbidden identifiers, real user paths) and fails CI on findings. It is a
guardrail, not a substitute for review.

# Release Checklist

Run through this before tagging a release. Every line must be *verified*, not
assumed — a green checkbox with no command behind it is not evidence.

---

## 1. Quality gates

```bash
# backend
uv sync --frozen --extra dev
uv run --frozen pytest -q
uv run --frozen pytest --cov=airi --cov-report=term-missing
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples

# frontend
cd airi-web
npm ci
npm run build
npm run test
cd ..
```

- [ ] Backend tests pass (no regressions below the previous baseline: 675 passed / 14 skipped)
- [ ] Coverage is reported and not materially lower than the previous release
- [ ] `ruff check` and `ruff format --check` are clean
- [ ] Frontend builds (`vue-tsc -b && vite build`) and unit tests pass (28)
- [ ] Skipped tests are still only the intentionally-skipped integration suites

## 2. Database

```bash
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic upgrade head
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic current
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/release_check.db" uv run --frozen alembic check
```

- [ ] `upgrade head` succeeds from an empty database
- [ ] `current` reports the expected head
- [ ] `check` reports no pending model/migration drift

## 3. Security and open-source safety

```bash
uv run --frozen python scripts/check_open_source_safety.py
cd airi-web && npm audit --omit=dev; cd ..
```

- [ ] Safety scanner passes (no internal domains, keys, or credentials)
- [ ] No real customer, company, or person names; no real identifiers
- [ ] No `.env` file or real secret is staged
- [ ] New dependencies reviewed; no unexplained major upgrades
- [ ] High/critical advisories triaged (documented if not fixed)

## 4. Local demo

```powershell
.\scripts\start-demo.ps1
# verify: http://localhost:5173 renders, http://localhost:8000/api/v1/meta responds
.\scripts\stop-demo.ps1
# verify: ports 8000 and 5173 are free afterwards
```

- [ ] One-command start works from a clean checkout
- [ ] Stop releases both ports
- [ ] Terminal demo runs: `uv run --frozen python examples/quick_demo.py`
- [ ] Both demo scenarios complete in the browser (Invoice Risk, Enterprise Relation)
- [ ] No unexpected `console.error` or 404 on key pages

## 5. Documentation

- [ ] README reflects the current feature set, Quick Start, and version
- [ ] Screenshots are from the current release, not an older UI
- [ ] `CHANGELOG.md` has an entry for this version
- [ ] `docs/architecture/` matches the code (no documented-but-absent modules)
- [ ] Version bumped consistently: `pyproject.toml`, `src/airi/__init__.py`
- [ ] `docs/guides/release-checklist.md` — this file — is current
- [ ] Unverified integrations still say so; see
      [Real Environment Checklist](real-environment-checklist.md)

## 6. Repository hygiene

- [ ] `LICENSE` present and unchanged in intent
- [ ] `.gitignore` covers `.env`, databases, build output, and local state
- [ ] `docs/screenshots/` **is** committed (not caught by a broad `*.png` rule)
- [ ] No build artefacts, caches, or `.demo/` content staged
- [ ] CI workflow green on the release commit

## 7. Claims audit

The hardest checklist, and the one that most often gets skipped:

- [ ] No statement implies production readiness
- [ ] No statement implies real financial performance
- [ ] Synthetic demo data is labelled as synthetic
- [ ] Unverified integrations are marked as unverified
- [ ] "Verified on Windows" claims were actually verified on Windows

## 8. GitHub repository metadata

Suggested description:

```text
AI-assisted risk metric research & development platform with Metric IR,
Skills, deterministic SQL generation, experiments, reflection and governed
metric lifecycle.
```

Suggested topics:

```text
ai-agent  llm  risk-management  fastapi  vue  pydantic
agentic-workflow  metric-engineering
```

- [ ] Description set
- [ ] Topics added
- [ ] `OWNER/REPO` placeholders in `README.md` replaced (or the CI badge removed)
- [ ] Repository is public and the default branch is `main`
- [ ] `docs/screenshots/` and `docs/demo/` are committed (not ignored)

## 9. Tag

```bash
git init
git add .
git commit -m "feat: release AIRI v1.0 portfolio edition"
git tag -a v1.0.0 -m "AIRI v1.0.0 — Portfolio Edition"
```

Push and create the GitHub release with notes from `CHANGELOG.md`.

> Pushing is a manual, deliberate step. Nothing in this repository pushes for you.

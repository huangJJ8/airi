# Quickstart

Run the full AIRI demo locally in one command. **No Spark, no MySQL, no LLM API
key, no internal network.** The default mode is SQLite + a deterministic demo
LLM substitute + bundled synthetic data, entirely offline.

---

## Prerequisites

| Tool | Requirement | Verified on |
| --- | --- | --- |
| Python | `>=3.12` (from `pyproject.toml`) | 3.13.12 |
| [uv](https://docs.astral.sh/uv/) | any recent version | 0.9.x |
| Node.js | `>=20.19` (Vite 8 requirement) | 22.22.2 |
| npm | ships with Node | 10.x |

Install missing tools from their official sites. The demo scripts never install
Python or Node for you, and never require administrator privileges.

---

## One-command demo

### Windows (primary, verified)

```powershell
git clone <your-repo-url>
cd airi

.\scripts\start-demo.ps1
```

The script:

1. checks that `uv`, `node`, and `npm` are on `PATH`, and that ports 8000/5173 are free
2. syncs backend dependencies (`uv sync --frozen --extra dev`)
3. runs migrations against a local SQLite demo database
4. seeds synthetic demo data through the real governed API chain
5. installs frontend dependencies on first run (`npm ci`)
6. starts backend + frontend and health-checks both
7. prints the URLs and writes PIDs to `.demo/demo-processes.json`

When it finishes you should see:

```text
== AIRI local demo is running ==
   Web UI      : http://localhost:5173
   API docs    : http://localhost:8000/docs
   Stop with  : .\scripts\stop-demo.ps1
```

Stop everything with:

```powershell
.\scripts\stop-demo.ps1
```

### macOS / Linux (best-effort, not runtime-verified in this repo)

```bash
git clone <your-repo-url>
cd airi

./scripts/start-demo.sh
./scripts/stop-demo.sh
```

> The shell scripts mirror the PowerShell flow, but the maintainers' verified
> environment is Windows. Treat the `.sh` variants as best-effort.

---

## Manual setup

If you prefer to run the steps yourself:

```bash
# 1. backend
uv sync --frozen --extra dev
cp .env.example .env                     # optional; the demo works with defaults
uv run --frozen alembic upgrade head
uv run --frozen python scripts/seed_demo.py

# 2. backend server
uv run --frozen uvicorn airi.main:app --host 127.0.0.1 --port 8000

# 3. frontend (second terminal)
cd airi-web
npm ci
npm run dev
```

Open <http://localhost:5173>.

---

## What to click

Both demo scenarios run through the **same** pages — `/development` →
`/testing` → `/experiments`.

| Scenario | Requirement | Capabilities |
| --- | --- | --- |
| **Invoice Risk** | 统计企业近30天开票金额 | `metric_sum`, `metric_window` |
| **Enterprise Relation** | 统计企业关联自然人控制的其他企业数量 | `metric_join`, `metric_count` |

On `/development`, use the **Demo Examples** dropdown to fill the requirement,
then Generate → Submit → Approve → continue to Testing → Experiment.

See the [Demo Guide](demo.md) for the full walkthrough.

---

## Verifying the install

```bash
# backend
uv run --frozen pytest                                  # 675 passed / 14 skipped
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts

# frontend
cd airi-web && npm run build && npm run test            # 28 passed
```

---

## Configuration

All settings are environment variables with the `AIRI_` prefix (see `.env.example`
and `airi-web/.env.example`). A few that matter for the demo:

| Variable | Demo value | Meaning |
| --- | --- | --- |
| `AIRI_DATABASE_URL` | `sqlite+pysqlite:///.demo/airi_web_demo.db` | local demo database |
| `AIRI_LLM_MODE` | `demo_mock` | deterministic LLM substitute, no network |
| `AIRI_EXECUTION_MODE` | `mock` | mock execution over synthetic fixtures |
| `AIRI_DEMO_FIXTURES` | `true` | load bundled synthetic datasets |
| `AIRI_CORS_ORIGINS` | `["http://localhost:5173"]` | allow the dev server origin |

An `.env` file is optional: process environment variables take precedence, and
`start-demo` sets everything it needs explicitly so the demo stays reproducible
even if your `.env` points at MySQL or a real LLM.

`AIRI_LLM_MODE=demo_mock` is a **deliberate substitute**, not a silent fallback.
If you configure a real LLM and it returns something the strict schema rejects,
AIRI raises an error — it never quietly degrades to the mock.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `[ERROR] 'uv' not found on PATH` | install uv, reopen the terminal |
| `[ERROR] Port 8000 is already in use` | run `.\scripts\stop-demo.ps1`, or free the port |
| Backend unhealthy after 60s | read `.demo/backend.log` |
| Frontend unhealthy after 60s | read `.demo/frontend.log` |
| Demo data looks stale | re-run `.\scripts\start-demo.ps1`; seeding rebuilds the demo DB |
| `alembic` tries to reach MySQL | set `AIRI_DATABASE_URL` to the SQLite URL above |

The demo database lives at `.demo/airi_web_demo.db` and is git-ignored. Deleting
it is safe — the next `start-demo` recreates and reseeds it.

---

## Next

- [Demo Guide](demo.md) — what each scenario demonstrates
- [Adding a Scenario](adding-scenario.md) — extend AIRI without forking it
- [API Guide](api.md) — domain endpoints

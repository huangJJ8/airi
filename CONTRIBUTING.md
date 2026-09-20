# Contributing to AIRI

Contributions are welcome. AIRI is a portfolio/research project, so the bar
for merging is: **understandable, deterministic where possible, and honest
about what is verified vs not.**

## Setup

```bash
# backend (Python 3.12+, managed with uv)
uv sync --frozen --extra dev

# frontend (Node 18+, npm)
cd airi-web
npm ci
```

## Development workflow

1. Create a branch for your change.
2. Make the change with tests.
3. Run the full quality gate (see below) - all of it must pass.
4. Open a pull request using the PR template.

## Backend tests

```bash
uv run --frozen pytest
uv run --frozen pytest --cov=airi --cov-report=term-missing
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples
uv run --frozen alembic check
```

Integration tests (`spark_integration`, `mysql_integration`,
`production_integration`, `identity_integration`, `telemetry_integration`)
intentionally **skip** without real infrastructure. A skip is the correct
outcome; never make them fake a pass.

## Frontend tests

```bash
cd airi-web
npm run build
npm run test
```

## Open-source safety check

Before every commit that touches content, run:

```bash
uv run --frozen python scripts/check_open_source_safety.py
```

Never commit real credentials, real customer data, real company names, or
real internal hostnames/table names. Demo data must stay synthetic.

## Adding a Scenario Skill

This is the extension path the architecture is built for. A new business
scenario (e.g. a new risk domain) should be added as **knowledge**, not as a
new workflow:

1. Add a Scenario Skill under `src/airi/skills/` describing the domain:
   entities, data sources, relationship semantics, business rules, known
   pitfalls, test expectations.
2. Reuse existing capability skills (`metric_sum`, `metric_count`,
   `metric_window`, `metric_join`, ...) where possible.
3. If a genuinely new *mechanism* is needed, add a **generic** capability
   skill (like `metric_join` was for Phase 11) - never a
   `<scenario>_sql_generator` style tool.
4. Extend the parser catalog / demo LLM mapping so the scenario can be
   reached from natural language, and **reject** ambiguous requirements
   instead of guessing.
5. Add a synthetic fixture with hand-calculated expected results.
6. Add tests: scenario selection, planner routing, SQL shape, and the
   hand-calculated numbers.

See [docs/guides/adding-scenario.md](docs/guides/adding-scenario.md) for the
full walkthrough using `enterprise_relation` as the worked example.

**Do not** do these:

- Copy the development/testing/experiment workflow for a new scenario.
- Put SQL strings or JOIN paths inside the Scenario Skill.
- Let the LLM produce final SQL directly (the Metric IR exists precisely to
  prevent that).
- Create scenario-specific SQL tools.

## Adding a Capability Skill

Capability skills encode *execution mechanics* (how to aggregate, how to
join, how to generate SQL), versioned independently of any scenario:

1. Declare the skill (id, version, inputs/outputs, constraints).
2. Implement or extend the deterministic Python tool / Jinja template.
3. Extend the static SQL validator allowlist if new SQL shapes are introduced.
4. Test the tool in isolation plus planner routing from at least one scenario.

## Pull request checklist

- [ ] `pytest` passes (no baseline test removed)
- [ ] `ruff check` and `ruff format --check` pass
- [ ] `airi-web`: `npm run build` and `npm run test` pass
- [ ] No sensitive data (run the safety scanner)
- [ ] Scenario vs Capability boundary preserved (no domain knowledge in tools, no SQL mechanics in scenario skills)
- [ ] No direct LLM-to-SQL path introduced
- [ ] Anything not actually verified is labeled SKIPPED / NOT VERIFIED, not silently green

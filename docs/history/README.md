# Engineering History

AIRI was built in phases, and each phase produced an implementation report
written *while* the work was done. They are preserved here because they are the
most honest record of the engineering decisions — including what was **not**
verified at each point.

The reports are development records, not product documentation. For how AIRI
works today, start with the [Architecture Overview](../architecture/overview.md).
For the design reasoning distilled to principles, see
[Design Principles](../architecture/design-principles.md).

---

## The arc

| Phase | Report | What it established |
| --- | --- | --- |
| 1.5 | [phase15-report.md](phase15-report.md) | First end-to-end slice: three metric cases (SUM, COUNT(\*), growth rate) through Mock LLM → IR → Planner → Jinja2 → static validator → persist → submit → approve. No production SQL executed. |
| 2 | [phase2-report.md](phase2-report.md) | Controlled execution and deterministic testing of *approved* metrics behind a gated service. Real Spark/Hive not yet connected. |
| 2.5 | [phase25-report.md](phase25-report.md) | Real-environment integration code, read-only probes, metadata validation, repeatable integration entry point. Real cluster acceptance: **NOT VERIFIED**. |
| 3 | [phase3-report.md](phase3-report.md) | Local single-metric experiment loop. Numbers come from a fixed synthetic sample under `mock` execution — not real financial validation. |
| 3.5 | [phase35-report.md](phase35-report.md) | Evidence-driven Reflection and proposal generation, using a real persisted synthetic experiment plus an explicit mock reflection LLM. |
| 4 | [phase4-report.md](phase4-report.md) | Controlled refinement loop: accepted research proposals become candidates, separately approved, re-tested, re-experimented, compared, and finally decided by a human. |
| 5 | [phase5-report.md](phase5-report.md) | Temporal validation and candidate promotion governance: PSI against a frozen reference distribution, threshold stability across slices, OOT evaluation — all deterministic Python, zero LLM calls. |
| 6 | [phase6-report.md](phase6-report.md) | Metric versioning, registry, and controlled release. `Artifact ≠ MetricVersion ≠ Released Version ≠ Production Deployed ≠ Active Registry Version`. |
| 7 | [phase7-report.md](phase7-report.md) | Production integration, monitoring, feedback loop. Registry active ≠ production active; deployed ≠ healthy; healthy today ≠ stable tomorrow. |
| 8 | [phase8-report.md](phase8-report.md) | Real production verification, trusted telemetry, auditable reconciliation. Its own summary: *Phase 8 didn't make AIRI smarter, it made AIRI harder to lie to.* |
| 9 | [phase9-report.md](phase9-report.md) | Operational reliability and governed convergence: who is wrong when registry, runtime, and expected state disagree; whether an already-satisfied action is a conflict or a completion; who delivers an alert and what happens when delivery fails. |
| 10 | [phase10-report.md](phase10-report.md) | Web MVP. Vue 3 + TypeScript front end over the existing API; backend frozen to a minimal surface. Product-language routing, zero frontend business computation. |
| 11 | [phase11-report.md](phase11-report.md) | Multi-scenario validation. A second, structurally different scenario (`enterprise_relation`) proving `New Scenario ≠ New Workflow`. Join IR added as an optional, backward-compatible IR extension with no migration. |
| 12 | [phase12-report.md](phase12-report.md) | Open source & portfolio release. README redesign, one-command local demo, architecture and design-principles docs, safety scanner, CI, Docker configuration (runtime `NOT VERIFIED`), demo GIF, and the v1.0.0 verdict. |

---

## How to read these reports

Two conventions run through all of them, and they are deliberate:

1. **Unverified is stated as unverified.** Paths that were never exercised
   against a real environment are marked `NOT VERIFIED` / `SKIPPED` rather than
   described as working. This includes real Spark/Hive, MySQL, production
   identity, and telemetry.
2. **Synthetic is labelled synthetic.** Every reported statistic comes from
   generated data, and the reports say so next to the number.

If you are evaluating the project, that discipline is part of the artefact. A
report that claimed more than it verified would undercut the entire design
premise — that AIRI's value is making claims *checkable*.

---

## Where the history lives now

```text
docs/
├── architecture/     how AIRI works today
├── guides/           quickstart, demo, extending, API, release
├── screenshots/      current UI, captured from the running demo
├── history/          ← these phase reports
└── portfolio.md      interview-facing design rationale
```

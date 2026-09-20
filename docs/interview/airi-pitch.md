# AIRI — Portfolio Pitch

Three versions. Use whichever fits the room. All three say the same thing at
different resolution.

---

## 30 seconds

> **The problem:** LLMs can write SQL, but a generated query is hard to trust,
> hard to review, and hard to govern — it has no spec to check against and it
> changes silently when the prompt changes.
>
> **What I built:** AIRI takes a natural-language risk requirement and compiles
> it into a strict structured metric IR, then uses deterministic Python tools —
> not the model — to generate the SQL. That SQL gets statically validated,
> automatically tested, run through a statistical experiment, and then handed to
> a human for approval at every gate.
>
> The model does requirement understanding and evidence interpretation. It never
> produces the artifact that ships. `LLMs reason. Python verifies. Humans govern.`

**Say the last line out loud.** It is the whole project in seven words.

---

## 3 minutes

### 1. Business problem (20s)

Risk teams need new indicators constantly. Today that is an analyst writing SQL
by hand — slow, inconsistent, and impossible to audit later. The obvious move is
"point an LLM at it." The problem is that raw generated SQL is not reviewable,
not reproducible, and not something you can safely promote into production.

### 2. Architecture (25s)

AIRI inserts a **reviewable boundary** between the model and the database:

```text
Natural Language
  → Metric IR               structured, schema-validated Pydantic
  → Skill Planner           scenario skill + capability skills
  → Tool Planner            deterministic Python tool
  → SQL                     content-addressed artifact
  → Validation              allow-list grammar
  → Testing                 automated metric checks
  → Experiment              coverage / KS / IV / lift / PSI
  → Reflection              hypotheses, stored separately from facts
  → Registry + Governance   immutable versions, human approval gates
```

### 3. Core workflow, and why Metric IR (35s)

You describe an indicator. The requirement parser returns a `MetricIR` — source
tables, time window, filters, aggregation, grouping, label definition,
evaluation spec. That object must satisfy a strict schema. If the model's output
does not parse, the run **fails loudly**; it does not silently repair, and it
does not fall back to a canned answer.

The IR is the thing a human can actually review. You cannot review SQL against
business intent, but you can review a structured metric definition.

### 4. Why not direct text-to-SQL (30s)

Three reasons, all practical:

1. **Reviewability.** No spec → no review. The IR is the reviewable object.
2. **Determinism.** The SQL is emitted by a fixed template from validated
   values. Same IR in, same SQL out — and the artifact is hashed, so the human
   approves the exact bytes that run.
3. **Testability.** Because the IR is structured, the platform knows what to
   test: schema conformance, null handling, duplicates, window boundary, join
   correctness, distinct semantics, self-exclusion, reconciliation.

### 5. Experiment and Reflection (25s)

Once a metric exists, Python computes the evidence: coverage, bad rate, decile
bins, KS with direction, IV, lift, threshold candidates, PSI against a frozen
reference. The LLM then gets those numbers and produces **hypotheses** — in a
separate table, never overwriting the computed facts. A reflection does not
change anything by itself. It becomes a bounded refinement proposal that a human
accepts or rejects.

### 6. Multi-scenario (15s)

The strongest evidence that this is not a single-demo hack: a second scenario,
`enterprise_relation`, needed a two-hop join — enterprise → person → enterprise,
with `COUNT DISTINCT` and self-loop exclusion. It reused the same workflow end to
end. To support it I promoted join handling into a **general capability**
(`metric_join@1.0.0`) rather than writing a scenario-specific tool.
`New Scenario ≠ New Workflow.`

### 7. Web demo (10s)

A Vue 3 UI drives the whole chain: dashboard, development, testing, experiment,
reflection, registry. Every KS / IV / lift number and every version state comes
from the backend API — the frontend does zero business computation.

---

## 10 minutes

Same as the 3-minute version, but with **architecture evolution** instead of a
straight feature list. Do **not** walk through phases one by one — tell it as
five capability stages, each one earning the next.

### Stage 1 — Metric compiler

*Established:* a requirement compiles into IR, IR compiles into SQL, SQL is
validated and executed read-only.

*Why it came first:* until the artifact is deterministic, nothing downstream can
be trusted. Testing a non-deterministic artifact is meaningless.

*What it bought:* a stable unit of review, and the ability to talk about "the
metric" as an object rather than a pile of SQL.

### Stage 2 — Experiment platform

*Established:* datasets, labels, evaluation windows, and a statistics engine —
coverage, decile bins, KS with direction, IV, lift, threshold candidates.

*Why second:* a metric that runs is not a metric that is *good*. Once the SQL is
deterministic, you can attach evidence to it.

*Design decision worth calling out:* comparability is enforced. Two runs are only
compared when dataset, label definition, evaluation window and snapshot line up —
otherwise the platform refuses the comparison instead of producing a misleading
number.

### Stage 3 — Reflection and refinement

*Established:* the LLM reads computed evidence and emits hypotheses; hypotheses
become bounded proposals; a human decides.

*Why here:* this is where most "agentic" systems go wrong — they let the model's
interpretation silently become the new configuration. AIRI keeps the two in
different storage with different types, so "the model thinks this is weak
separation" can never be mistaken for "separation is weak."

### Stage 4 — Governance and registry

*Established:* immutable metric versions, audit events, promotion/release/
deployment review gates, rollback, temporal (PSI / OOT) validation, and
production adapter boundaries that **fail closed** by default.

*Why last among the backend stages:* governance is only meaningful once there is
something worth governing, and once the evidence chain exists to justify a
decision.

### Stage 5 — Web UI and multi-scenario

*Established:* a Vue 3 product UI over the same API, and a second scenario that
proves the pipeline generalizes.

*Why it is the closer:* it is the demo, and it is the falsifiable claim. Two
scenarios, one workflow, no forked orchestration — and a general `metric_join`
capability instead of a special case.

### Then close with the honest part (60–90s)

*"Here is what I did not verify, and why that matters."*

- All bundled data is synthetic. The statistics demonstrate the pipeline, not
  predictive power.
- The Spark/Hive and production adapters are written and boundary-tested, but
  never run against a real cluster — there was none available. They are marked
  unverified and they default to inert.
- Docker configuration is provided and reviewed but **NOT VERIFIED** — Docker
  was not available in the development environment.
- The integration suites for Spark, MySQL, production identity and telemetry
  are **skipped**, not mocked into green.

> An interviewer has heard "it works" a hundred times. Being precise about the
> boundary of what you verified — and having that precision wired into the CI
> and the wording — is the thing that reads as senior.

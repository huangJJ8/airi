# Design Principles

Five boundaries shape every decision in AIRI. They exist because a language
model is useful for *understanding intent* and dangerous for *producing
irreversible artefacts*, and because research findings are not production
authorisations.

---

## 1. The LLM never owns the final SQL

```text
LLM  →  Metric IR  →  Deterministic tool  →  SQL
```

A model may propose *what* is being measured. It may not write the query that
measures it.

**Why.** Text-to-SQL is easy to demo and hard to trust. A raw generated query
cannot be reviewed against a spec, cannot be diffed meaningfully, and silently
changes when the model or prompt changes. By forcing the model's output through
a strict schema, the SQL becomes a pure function of a reviewable intermediate
representation:

```python
sql = generate_spark_sql_metric(metric_ir)   # template + allow-list, no model
```

**Consequences.**

- The same IR always yields byte-identical SQL — the canonical hash is stable.
- A reviewer reads the IR, not a 60-line query.
- Prompt or model changes cannot alter emitted SQL unless the IR changes first.
- If the model emits something the schema rejects, the run fails. AIRI does not
  repair it, and it does not fall back to a canned answer.

The prompt itself carries a structured representation of the expected IR
(including the relation join shape), so the model's job is *translation into a
known grammar*, not free-form invention.

---

## 2. Facts versus hypotheses

```text
Statistics   → deterministic, recomputable, evidence
Reflection   → probabilistic, narrative, a hypothesis
```

**Why.** A convincing paragraph about why a metric is good is not evidence. If
narrative and number live in the same place, the narrative eventually wins.

**Consequences.**

- Coverage, KS, IV, lift, bins, and threshold candidates are computed by Python
  from the sample. Re-running reproduces them exactly.
- Reflection output is stored as a *separate artefact type* with its own schema.
  It can cite statistics; it can never overwrite them.
- Reflection may propose `source_field_review` or `business_rule_review`. It has
  no capability to modify an IR, a threshold, or a label definition directly.

---

## 3. Scenario knowledge ≠ execution mechanics

```text
Domain knowledge   →  Scenario Skill
Execution mechanics →  Capability Skill  (+ pinned Tool)
```

**Why.** If business meaning leaks into the SQL layer, every new domain forks
the pipeline. If mechanism leaks into the domain layer, you cannot reuse it.

**Consequences.**

- A scenario skill declares business semantics, entity semantics, data-source
  semantics, relationship semantics, field interpretation, business rules, and
  known pitfalls.
- A scenario skill does **not** contain SQL, join templates, or dialect details.
- A capability skill declares *how* a mechanism is realised and which pinned
  tool implements it. It declares no business meaning.
- `metric_join` is the deliberate proof: it composes two structured sources
  through constrained equality joins and knows nothing about enterprises,
  persons, or risk. The `enterprise → person → enterprise` path is scenario
  knowledge.

This is what makes Phase 11's result meaningful — adding a second, structurally
different scenario required **zero new workflow**.

---

## 4. Research ≠ production

```text
Experiment passed  ≠  Production released
```

**Why.** A metric that separates good from bad on a synthetic (or even real)
sample has not been shown to be stable, monitored, or safe to serve. Collapsing
these two states is how unvalidated numbers reach a decision surface.

**Consequences.**

AIRI models the lifecycle as a chain of explicit, human-gated transitions:

| Stage | Question answered |
| --- | --- |
| Development | Is the metric expressible and is the SQL well-formed? |
| Testing | Does it behave correctly on the edge cases we care about? |
| Experiment | Does it separate outcomes on a sample, and by how much? |
| Reflection | What might be wrong with it? |
| Refinement | Should a bounded change be proposed? |
| Temporal / OOT | Does it still hold out of time? |
| Registry | Which immutable version is the approved one? |
| Release / Deployment | Is it authorised to run, and is it observed? |

Every stage records its own evidence. Nothing auto-promotes.

---

## 5. Humans govern

```text
SQL approval
Promotion review
Release review
Deployment review
```

**Why.** The system is designed to *assist* a risk analyst, not to replace the
accountability of one.

**Consequences.**

- Generated SQL is inert until a human approves the exact content hash.
- Promotion, release, and deployment are separate approvals with separate
  reviewers. Approving SQL does not approve production use.
- Approved artefacts are immutable. A change means a new version — the audit
  trail never rewrites history.
- Governance errors surface as explicit HTTP 409 conflicts with a machine-
  readable `error_code`, so the UI shows the real reason instead of a generic
  failure.

---

## Related reading

- [Architecture Overview](overview.md) — pipeline and component map
- [Quickstart](../guides/quickstart.md) — run it locally
- [Adding a Scenario](../guides/adding-scenario.md) — extend it without forking it
- [Portfolio notes](../portfolio.md) — why these decisions, in interview form

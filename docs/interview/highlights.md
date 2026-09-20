# AIRI — Five Things Worth Remembering

If an interviewer remembers nothing else about AIRI, these are the five.

---

## 1. Metric IR — the boundary that makes LLM output reviewable

A natural-language requirement is compiled into a **strict, versioned Pydantic
contract** before anything touches a database. The model may propose; only the
schema admits.

`src/airi/metric_ir/` · `MetricIR` carries source, window, filters, aggregation,
grouping, joins, label definition and evaluation spec — each field itself
validated against an identifier pattern or an allow-list.

**The point:** you cannot review raw generated SQL against a business intent, but
you can review a structured IR. The IR is the artifact a human actually approves.

---

## 2. Scenario Skill × Capability Skill — domain vs. mechanism

The platform's one genuine architectural bet about extensibility:

```text
Scenario Skill   (what the business means)
   invoice_risk        @1.0.0
   enterprise_relation @1.0.0
        │  declares which mechanisms it needs
        ▼
Capability Skill (how that mechanism is realised, and which tool pins it)
   metric_window @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_sum    @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_count  @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_growth_rate @1.0.0 → generate_growth_rate_sql @1.0.0
   metric_join   @1.0.0 → generate_spark_sql_metric @1.3.0
```

`src/airi/skills/` · business semantics never enter the SQL layer; execution
mechanics are declared once in `capabilities.py` and reused.

**The point:** adding a second scenario meant adding *one scenario file*. The
join machinery it needed was promoted to a general capability, not written as a
scenario special case — see §Answer in [airi-qa.md](airi-qa.md) on `metric_join`.

---

## 3. Deterministic SQL generation — the model never writes the query

SQL comes from **controlled Jinja templates** (`src/airi/tools/templates/`),
filled with IR values that survived validation, then passed through a
**static allow-list grammar** before it can be executed or approved:

- `INNER JOIN` / `LEFT JOIN` only
- equality predicates only in join conditions
- no `UNION`, no CTE injection, no UDF, no DDL/DML
- read-only execution sandbox

The generated artifact is **content-addressed**; the human approves a hash, so
the query that ships is byte-identical to the query that was reviewed.

**The point:** this is the difference between "the LLM generated SQL" and "the
LLM filled in a form, and Python wrote the SQL."

---

## 4. Experiment + Reflection — separating computed fact from model opinion

Statistics are computed in Python and stored as **facts**: coverage, bad rate,
decile bins, KS with its direction, IV, lift, PSI, threshold candidates.

The LLM is then given those facts and asked for **hypotheses** — stored in a
separate table, a separate type, and a separate UI section. Hypotheses never
overwrite facts, and **a reflection never activates a change by itself**;
it becomes a bounded, human-decided refinement proposal or it goes nowhere.

**The point:** this is how you let a model reason about evidence without letting
it launder an opinion into a metric definition.

---

## 5. Human governance at every gate that matters

Approval is not a checkbox at the end. It is a set of distinct, recorded gates:

```text
SQL approval → promotion review → release review → deployment review
```

Metric versions are **immutable** in the registry, carry their audit-event
stream, and support rollback. A test count, a statistic, or a model conclusion
is never itself a permission to promote.

**The point:** the slogan is a design constraint, not a tagline —
*LLMs reason. Python verifies. Humans govern.*

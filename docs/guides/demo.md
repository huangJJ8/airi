# Demo Guide

AIRI ships two structurally different demo scenarios that run through the
**same** product workflow. That is the point: a new business domain is added by
supplying scenario knowledge, not by forking the pipeline.

> Everything below uses **synthetic data** generated in-repo. No real company,
> person, invoice, or identifier is involved. Results are illustrative only and
> are **not** production evidence.

---

## Why two scenarios

| | Invoice Risk | Enterprise Relation |
| --- | --- | --- |
| Business semantics | billing amount over time | control relationships between enterprises |
| Data shape | one fact table | two tables joined on a person |
| Entity path | enterprise → invoice facts | enterprise → person → enterprise |
| Mechanics | time window + `SUM` | join + `COUNT DISTINCT` |
| Capabilities | `metric_window`, `metric_sum` | `metric_join`, `metric_count` |
| Shared | Requirement Parser, Metric IR, Skill Planner, Tool Planner, SQL Generator, Validator, Approval, Testing, Experiment, Web UI | ← same |

If a single pipeline can absorb both without a second workflow, the
Scenario Skill × Capability Skill split is doing real work.

---

## Demo A — Invoice Risk

### Input

```text
统计企业近30天开票金额
```

### Expected resolution

| Step | Result |
| --- | --- |
| Scenario | `invoice_risk@1.0.0` |
| Capabilities | `metric_window`, `metric_sum`, `spark_sql_generator` |
| Metric IR | `entity_key: enterprise_id`, `aggregation: sum(amount)`, `window: 30 natural days`, explicit anchor |
| SQL | single-table windowed `SUM` |

### What to look at

- **Development** — the Metric IR viewer shows the window (left-closed,
  right-open, `Asia/Shanghai`) and the summed field.
- **Testing** — window boundary semantics, schema, null/duplicate handling.
- **Experiment** — coverage, KS, IV, lift, bins, threshold candidates.
- **Reflection** — narrative reading of the statistics, plus bounded proposals.
- **Registry** — an immutable governed version.

---

## Demo B — Enterprise Relation

### Input

```text
统计企业关联自然人控制的其他企业数量
```

### Expected resolution

| Step | Result |
| --- | --- |
| Scenario | `enterprise_relation@1.0.0` |
| Capabilities | `metric_join`, `metric_count`, `spark_sql_generator` |
| Relationship path | `enterprise → person → enterprise` (two hops) |
| Aggregation | `COUNT DISTINCT related_enterprise_id` |
| Business rule | self-exclusion — `related_enterprise_id <> enterprise_id` |

### Generated SQL (shape)

```sql
SELECT
    ep.enterprise_id AS entity_id,
    COUNT(DISTINCT pe.related_enterprise_id) AS related_enterprise_count
FROM demo.enterprise_person_relation ep
INNER JOIN demo.person_enterprise_relation pe
    ON ep.person_id = pe.person_id
WHERE
    pe.related_enterprise_id <> ep.enterprise_id
GROUP BY
    ep.enterprise_id
```

This is produced by the shared deterministic tool from the IR — the workflow
never builds SQL strings.

### What to look at

- **Development** — the IR viewer now shows a `joins` block, plus a Relationship
  summary line (`enterprise → person → enterprise`) and `COUNT DISTINCT`.
- **Testing** — the test list is scenario-specific and delivered by the backend:
  *Join Correctness*, *Distinct Count*, *Self Relation Exclusion*. There is no
  window test, because this metric has no window.
- **Experiment** — reuses the same evaluation engine (`coverage`, KS, IV, lift,
  bins, thresholds) with this metric's data. No second evaluation stack.

### Edge cases exercised by the bundled fixture

The synthetic relation dataset is built so that these all occur, and the
automated tests assert each:

| Case | Expected behaviour |
| --- | --- |
| Same relation row duplicated | `COUNT DISTINCT` prevents double counting |
| Two persons → the same other enterprise | counted once, not twice |
| Person whose relation loops back to the enterprise itself | excluded by self-exclusion |
| Enterprise with no relations | not present in results (no fabricated zero row) |

Hand-computed expectations live in the fixture and are written by hand — the
tests never call production code to derive the expected answer.

---

## Walking through the UI

Both demos follow the identical path. **You never copy/paste a UUID** — the
context travels via the route query and a session-scoped store.

```text
/development   pick a Demo Example → Generate → review IR / Skills / SQL
               → Submit → Approve
     ↓
/testing       Run automated tests → per-test status + evidence
     ↓
/experiments   Run experiment → coverage / KS / IV / lift / bins / thresholds
               → Analyze (Reflection) → proposals
     ↓
/registry      governed metric version
```

### Honest-mode markers

The UI deliberately keeps these visible. They are a feature, not clutter:

- `LOCAL DEMO`
- `Synthetic Data`
- `NOT PRODUCTION VERIFIED`
- `Browser does not generate or modify SQL.`

---

## What the demo does *not* claim

- Not a production risk system.
- Synthetic data is not risk evidence.
- The experiment numbers are illustrative of the *pipeline*, not of predictive
  power on real portfolios.
- Enterprise Spark/Hive/production adapters exist in code but are **not
  verified** in this repository.

---

## Next

- [Quickstart](quickstart.md) — get it running
- [API Guide](api.md) — drive it from HTTP
- [Adding a Scenario](adding-scenario.md) — how Demo B was added

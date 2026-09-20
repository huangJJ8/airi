# Adding a Scenario

This guide is a case study: how `enterprise_relation` was added as the second
scenario, and what that implies for the next one.

The result to aim for is stated negatively, because that is the hard part:

> **New Scenario ≠ New Workflow.**

If adding a scenario requires a new pipeline, a new SQL builder, or a
scenario-specific endpoint, the abstraction has failed.

---

## The rule

```text
Scenario Skill      → business semantics        (domain knowledge)
Capability Skill    → execution mechanics       (mechanism)
Tool                → deterministic realisation (pinned by a capability)
```

Ask: *would another domain plausibly need this?*

- "Enterprise → person → enterprise" → **scenario knowledge**. Nothing else
  needs it.
- "Join two structured sources on equality" → **capability**. Many domains need it.
- "Render a join into validated SQL" → **tool**. One implementation, pinned.

Getting this wrong in either direction is the failure mode: business meaning in
the SQL layer forks the pipeline; mechanism in the domain layer can't be reused.

---

## Step by step (as done in Phase 11)

### 1. Scenario skill — semantics only

Add `src/airi/skills/<scenario>.py`. It declares *what the business means* and
nothing about SQL:

```python
KNOWLEDGE = [
    "业务域：企业关联关系风险（enterprise relationship risk）。",
    "主体：enterprise（企业），主体字段 enterprise_id。",
    "关系路径：enterprise -> person -> enterprise（两跳，不做多跳与股权穿透）。",
    "业务规则：自身排除——关联企业等于主体企业时不计入。",
    "业务规则：同一企业由多个自然人共同指向时仍只计一次（按企业去重）。",
    "注意事项：直接按 enterprise_id 关联 related_enterprise_id 会跳过自然人中转，语义错误。",
]

TEST_RULES = ScenarioTestRules(
    entity_null=TestRule(severity="warning"),
    result_duplicate=TestRule(severity="error"),
    empty_result=TestRule(severity="warning"),
    test_types=["schema", "null", "duplicate", "join", "distinct",
                "self_exclusion", "missing_relation", "reconciliation"],
)

def enterprise_relation_skills():
    capabilities = [metric_join(), metric_count(), spark_sql_generator()]
    scenario = ScenarioSkill(
        name="enterprise_relation",
        version="1.0.0",
        intent="研发企业关联自然人控制或参股的其他企业数量（related_enterprise_count）",
        capabilities=[VersionedReference(name=s.name, version=s.version) for s in capabilities],
        knowledge=KNOWLEDGE,
        test_rules=TEST_RULES,
        requires_human_review=True,
    )
    return [*capabilities, scenario]
```

Note what is **absent**: no join syntax, no alias handling, no dialect, no SQL.
`test_types` declares which *categories* of test this domain needs — the backend
turns them into the actual test list the UI renders.

### 2. Register it

Add the skill to the registry wiring in `src/airi/main.py` alongside
`invoice_risk`. The registry pins `name@version`; there is no "latest".

### 3. Reuse capabilities — do not fork them

`metric_join` already existed as a *generic* capability, so the scenario simply
references it. `metric_count` already supported `COUNT(DISTINCT field)`.

If `metric_count` had not supported distinct counting, the right fix would be a
minimal extension to the existing capability (`distinct: true`) — **not** a new
`enterprise_relation_count_skill`. Re-coupling business and mechanism is exactly
what this architecture exists to prevent.

Only add a new capability when the *mechanism* is genuinely new and reusable.
If it is new but not reusable, it is scenario knowledge in the wrong place.

### 4. Extend the IR, not the workflow

`MetricIR` gained optional `joins: list[JoinSpec]` and
`column_filters: list[FieldComparison]` (`src/airi/metric_ir/joins.py`).

```text
JoinSpec
├── alias        : Identifier
├── join_type    : "inner" | "left"          # only these two
├── source       : DataSource
└── conditions   : list[FieldComparison]      # 1..4, operator "=" or "<>"
```

Design constraints that kept the change additive:

- fields are **optional with empty defaults** → existing IRs still validate
- `schema_version` stays `1.0.0` → no migration, joins live in the JSON document
- **no DAG**, no recursive joins, no `RIGHT`/`FULL`/`CROSS`/`LATERAL`
- the canonical hash **includes** `joins`, so a different relationship path
  cannot collide with an existing hash

Wanting a `MetricIRV2` is usually a sign the change wasn't modelled as optional.

### 5. Teach the parser the new vocabulary

Two places, deliberately different:

- **Demo LLM** (`src/airi/infrastructure/demo_llm.py`) — a deterministic mapping
  from the demo phrase to a structured result. Explicitly *not* a pattern match
  inside the real parser.
- **Real parser prompt** (`src/airi/agents/requirement_parser/prompts.py`) — a
  structured description of the new scenario and the expected join shape, so a
  real LLM translates into the known grammar.

Unknown or ambiguous requirements must be **rejected**, not guessed. In Phase 11
the ambiguous phrase 「统计企业关联数量」 is refused, and cross-scenario phrases
fail scenario isolation.

### 6. Synthetic fixture with hand-computed expectations

Add a synthetic dataset (`src/airi/infrastructure/relation_fixture.py`) that
deliberately contains the awkward cases:

| Case | Why |
| --- | --- |
| duplicated relation row | proves `COUNT DISTINCT` |
| two persons → same other enterprise | proves de-duplication is by enterprise |
| relation looping back to the subject enterprise | proves self-exclusion |
| enterprise with no relations | proves "no fabricated zero row" |

Expected values are written **by hand**. Tests never call production code to
derive the expected answer — otherwise the test just asserts the code equals
itself.

### 7. Tests

Add `tests/test_<scenario>.py` covering the declared `test_types` plus
regressions that matter beyond this scenario:

- join correctness — the path is `enterprise → person → related_enterprise`,
  **not** a direct `enterprise_id → related_enterprise_id` shortcut
- distinct semantics
- self-relation exclusion
- null / duplicate entity
- missing relation
- reconciliation

Then add **planner regression** and **scenario isolation** tests:

- `invoice_risk` plans to `metric_sum + metric_window`
- `enterprise_relation` plans to `metric_join + metric_count`
- neither leaks into the other

### 8. Wire the demo surface

- `scripts/seed_demo.py` — seed the new scenario through the *real* governed
  API chain (not by inserting rows directly)
- `examples/demo_phase11.py` — terminal demo running both scenarios side by side
- Web `Demo Examples` dropdown — one entry, backed by the same pages

No new page. `/development`, `/testing`, `/experiments` are reused.

---

## Checklist

- [ ] Scenario skill added with semantics, `test_types`, and human-review flag
- [ ] Skill registered (pinned version, no "latest")
- [ ] Existing capabilities reused; extensions kept generic
- [ ] IR change is optional/backward-compatible (or a migration + version bump is justified)
- [ ] Canonical hash covers the new fields
- [ ] Demo LLM + real prompt updated; unknown/ambiguous input still rejected
- [ ] Synthetic fixture includes duplicated / self / multi-person / empty cases
- [ ] Hand-computed expected values in tests
- [ ] Planner regression + scenario isolation tests
- [ ] Seed script + demo script updated
- [ ] Web shows the new scenario **without a new page**

## Anti-patterns

| Don't | Why |
| --- | --- |
| `generate_<scenario>_sql` tool | scenario-specific SQL forks the generator |
| Business phrases inside SQL templates | business meaning belongs in the scenario skill |
| `MetricIRV2` for an additive field | optional fields + JSON document column keep it versionless |
| Copying a workflow per scenario | the whole point is one workflow |
| `if "关联企业" in requirement` in the real parser | hardcoded phrases don't generalise; use the skill catalog / structured prompt |
| Tests that compute expectations with production code | asserts nothing |

---

## Next

- [Architecture Overview](../architecture/overview.md) — how the pieces fit
- [Design Principles](../architecture/design-principles.md) — why the split exists
- [Demo Guide](demo.md) — see both scenarios run

# AIRI — Technical Interview Q&A

22 questions an interviewer is likely to ask about AIRI, with the answer I would
actually give. Answers are deliberately specific — vague answers to these
questions are what makes a portfolio project sound like a tutorial.

---

## 1. Why not go straight from the requirement to text-to-SQL?

Because generated SQL is the wrong **unit of review**.

Three concrete failures: it has no spec to validate against, so a reviewer can
only eyeball a wall of joins; it is not reproducible, so the same requirement can
produce a different query next week; and it has no clean approval point — you
cannot say "this is the query I approved" about a string that regenerates itself.

AIRI compiles the requirement into a structured `MetricIR` instead, and *Python*
emits the SQL from a controlled template. The model's job ends at "what does the
user mean"; the artifact that ships is produced deterministically from a
validated object.

## 2. What is the value of Metric IR, specifically?

Four things, in order of importance:

1. **It is reviewable.** A human can diff a structured metric definition against
   a business intent. You cannot diff SQL against intent.
2. **It is testable.** Because the IR declares source, window, filters,
   aggregation, grouping and label definition as fields, the test layer knows
   what to assert — window boundaries, null handling, duplicate keys, distinct
   semantics, join correctness — instead of guessing.
3. **It is stable under model changes.** Change the model or the prompt and the
   IR may change; but if the IR is unchanged, the SQL is byte-identical.
4. **It is versioned.** The registry stores metric versions keyed to the
   structure, not to a string of text.

## 3. What is the difference between a Skill and a Tool?

A **Skill** is declarative knowledge: what a mechanism means, what it requires,
and which tool it pins. A **Tool** is imperative code: the thing that actually
produces SQL, registers a template, and returns an artifact.

Concretely: `metric_sum@1.0.0` is a capability skill that says "this is an
aggregation over a window with these required IR fields" and points at
`generate_spark_sql_metric@1.1.0`. The tool is the Python class that renders
`spark/atomic_metric.sql.j2` and returns the SQL plus its hash.

The split matters because **the registry can validate the declaration without
importing or executing the mechanism**. A skill is data you can audit; a tool is
code you have to trust.

## 4. Scenario Skill vs. Capability Skill?

- **Scenario Skill** — *what the business means.* Owns domain semantics,
  terminology, the intent, and which mechanisms this domain needs. Two exist:
  `invoice_risk@1.0.0` and `enterprise_relation@1.0.0`.
- **Capability Skill** — *how a mechanism is realised.* Owns the mechanism's
  contract and the tool version that implements it. Shared across scenarios.

The test of the split: a **new scenario should not need a new mechanism**. The
second scenario needed two-hop joins — that required a new *capability*
(`metric_join@1.0.0`), not a new scenario-specific tool, and once it existed both
scenarios could reference it. Business semantics stay out of the SQL layer.

## 5. How do you prevent the LLM from hallucinating SQL?

I never let it write SQL in the first place. The prevention stack, outer to
inner:

1. **The model's output type is not SQL.** It returns a Pydantic model
   (`MetricIR`). There is no code path that accepts SQL text from the model.
2. **Strict schema validation.** `extra="forbid"`, identifier patterns
   (`^[a-z][a-z0-9_]{0,63}$`), enum-constrained join types and operators. A
   response that does not fit raises — it is not repaired.
3. **No silent fallback.** An invalid response fails the run loudly. It never
   degrades to a canned answer, so a "successful" run always means the model
   actually produced a valid IR.
4. **Template-only SQL emission.** SQL is rendered from fixed Jinja templates
   with validated values interpolated.
5. **Static grammar validation on the output.** Even though Python wrote it, the
   SQL still has to pass an allow-list check — `INNER`/`LEFT` joins only,
   equality predicates only, no `UNION`, no CTEs, no UDFs, no DDL/DML.
6. **Read-only execution sandbox.**

Belt, braces, and a locked door. The hallucination risk is not "the model writes
bad SQL" — it is "the model writes plausible IR"; that is the layer I defend.

## 6. Why Pydantic specifically?

Because the IR boundary is the project's core control, and Pydantic is a
**declarative, self-documenting, runtime-enforced** way to express it. One class
definition gives me: validation, coercion, JSON schema, clear error messages, and
a serialisation format that round-trips into the registry.

Two details that mattered in practice:

- **Strict modes.** `extra="forbid"` and explicit patterns mean a model that
  invents a field fails rather than getting silently ignored.
- **Typed decoding of persisted data.** The registry round-trips IR through JSON;
  the decoder is the same schema, so stored artifacts cannot drift out of
  validity.

The alternative — hand-written validators — would be more code, less obvious, and
would drift from the docs.

## 7. Why not LangGraph?

> The current workflow's control path is **deterministic**. Ordinary Python
> orchestration is easier to test, easier to audit, and easier to debug than a
> graph runtime — so I did not introduce LangGraph just to have an agent
> framework in the stack. If the control flow later becomes genuinely dynamic —
> loops whose shape depends on runtime evidence — that is the point at which a
> graph runtime would start paying for itself.

The workflow here is a fixed pipeline with explicit gates. The complexity lives
in the *domain governance*, not in the control flow, so a framework would add a
dependency and an abstraction without removing any of the real work.

## 8. Why not multi-agent?

> AIRI's complexity comes from **domain governance and evidence chain**, not from
> the number of roles. Splitting the system into more agents would not
> automatically make it more reliable — it would just distribute the same
> decisions across more hops and make the failure modes harder to trace. So v1.0
> invests in structured IR, deterministic tools, and a governed workflow instead.

A concrete illustration: if a "reflection agent" could write directly to the
registry, adding an agent would have *removed* a governance boundary. The
current design keeps reflection read-only over facts and forces its output
through a human decision.

## 9. What is Reflection in this system, precisely?

Reflection is an **evidence-interpretation step**. Python computes the facts
(coverage, bad rate, decile bins, KS and its direction, IV, lift, PSI, threshold
candidates). Those facts are handed to the LLM, which returns **hypotheses**:
what the evidence might mean, and what to try next.

It is stored in its own model and its own table. It is not a metric version, not
a configuration, and not a dataset.

## 10. Why don't reflection conclusions take effect automatically?

Because a hypothesis is not evidence, and the failure mode is silent.

If reflection could write to the metric definition directly, then "the model
thinks separation is weak" would become "separation is weak" — an opinion
laundering itself into a governed artifact, with no trace of which one it was.
Worse, it would be untestable: the metric would change for reasons that are not
in the evidence.

So reflection output is **read-only relative to the metric**. It can produce a
bounded refinement proposal; a human accepts or rejects it. The facts and the
hypotheses live in different storage with different types, which is what makes
the distinction enforceable rather than merely documented.

## 11. How are KS / IV / Lift actually used here?

As **evidence for a decision**, never as a decision.

- **Coverage / bad rate** — is this metric even applicable, and to how much of
  the population? A high-IV metric on 2% of records is a headline, not a signal.
- **KS with direction** — separation, *plus* which way it points. Direction is
  tracked because a strongly separating metric that points the wrong way is a
  finding, not a success.
- **IV** — per-feature predictive strength.
- **Lift / decile bins** — is the relationship monotonic across the risk
  ordering, or is it concentrated in one bin?
- **Threshold candidates** — *research candidates*, explicitly not recommended
  production thresholds.
- **PSI** — drift against a frozen reference, computed in deterministic Python
  with zero LLM calls.

The platform also **refuses to compare incomparable runs** — different dataset,
label definition, evaluation window or snapshot means no comparison, because a
misleading number is worse than no number.

## 12. Why did you pick enterprise relationships for the second scenario?

Because it is structurally *different* in exactly the way that stress-tests the
architecture, while staying in the same risk domain.

The first scenario (`invoice_risk`) is single-source aggregation with a window.
The second (`enterprise_relation`) is a **two-hop relationship**:
enterprise → person → enterprise, with `COUNT DISTINCT` and a self-loop
exclusion (`related_enterprise_id != enterprise_id`).

If the architecture had only worked for single-source aggregation, a second
single-source scenario would have hidden that. A join scenario forces the
question: *is the IR general, or did I just fit it to scenario one?* — and it
produces a falsifiable claim: two scenarios, one workflow, no forked
orchestration.

## 13. How did you keep the join general instead of scenario-specific?

By refusing to write it as `enterprise_relation_join`. Instead:

- **IR level** — `MetricIR` gained optional `joins`, `column_filters`,
  `source_alias`, `aggregation_alias`. All optional, so the schema version stayed
  `1.0.0` and **no migration was needed**.
- **Join IR** — a `JoinSpec{left_source, right_source, join_type, conditions[]}`
  with a deliberately narrow grammar: `INNER`/`LEFT` only, equality only.
- **Capability level** — a new general capability `metric_join@1.0.0` pinning
  `generate_spark_sql_metric@1.3.0` and template `spark/join_metric.sql.j2`.
- **Validation** — a closed grammar for join SQL, plus a static validator that
  rejects anything outside it.
- **Canonical hashing** — extended to include joins, so a changed join changes
  the artifact hash. Otherwise the approval hash would not cover the join, which
  would be a governance hole.

The generalisation test: the second scenario references `metric_join`, and so
could a third — without touching the tool.

## 14. If a third scenario had to be added, what would it take?

Depends on whether it needs a new *mechanism*:

- **Same mechanisms, new domain semantics** — one scenario skill file
  (`src/airi/skills/<name>.py`) declaring its capabilities and knowledge, plus a
  synthetic fixture. No IR change, no tool change, no migration. Follow
  [Adding a Scenario](../guides/adding-scenario.md), which also lists the
  anti-patterns.
- **New mechanism** (say, a window-function ranking) — a new capability skill
  pinning a new tool, a new tool class, a new template, a new entry in the static
  SQL validator's allow-list, and probes in `src/airi/testing/`.

What would *not* change: the requirement parser, the workflow, the experiment
layer, the registry, and the UI. That is the point of the
Scenario × Capability split — and it is why the second scenario is the evidence,
not the feature list.

## 15. Why is there no real Spark here?

> This is a portfolio project running on a local Windows machine, with no
> enterprise Spark/Hive cluster available. So AIRI defaults to SQLite, synthetic
> data, and a mock adapter. The system **keeps the adapter interface and the
> fail-closed verification boundary**, but it does not package a mock result as a
> production-verified one.

Concretely: the Spark SQL executor and the production adapters exist and are
exercised at the boundary layer, but they are marked **unverified**, and the
identity providers and production adapters **default to inert** so a
misconfigured deployment fails closed rather than trusting a client-supplied
name or a synthetic runtime.

I would rather ship a project that states its boundary precisely than one that
implies a capability it never ran.

## 16. How do you keep mock and real production distinguishable?

Six mechanisms, all of them structural rather than conventional:

1. **Explicit configuration, not fallback.** `AIRI_LLM_MODE=demo_mock` is a
   deliberate substitute that must be *set*. If you configure a real endpoint and
   it returns something schema-invalid, the run raises — it does not quietly
   degrade to the mock. Demo mode is never a silent fallback.
2. **Fail-closed default.** Production adapters and identity providers default to
   inert.
3. **Adapter interfaces.** Real integrations are implementations behind an
   interface, so the mock is not a special case inside business logic.
4. **Honest markers.** Screenshots and the demo carry `LOCAL DEMO` /
   `Synthetic Data` / `NOT PRODUCTION VERIFIED`, and they are kept visible on
   purpose.
5. **Skipped, not mocked.** Integration suites that need real infrastructure are
   reported as **skipped** — they are never faked into a green result.
6. **Wording discipline.** The README distinguishes "code-complete and
   boundary-tested" from "verified against a real environment". Those are
   different claims and the docs never merge them.

## 17. How would you connect it to a real Spark/Hive cluster?

The seams already exist; the work is operator-driven verification, not
architecture:

1. Implement (or configure) the query executor for the real engine behind the
   existing executor interface.
2. Point `AIRI_DATABASE_URL` at the real metastore/warehouse config and switch
   the execution mode off `mock`.
3. Implement the real identity provider so approvals are attributed to real
   actors — the protected requirements around actor authentication are
   non-waivable by design.
4. Run the environment acceptance path (`src/airi/environments/`) against the
   cluster and record the result.
5. Walk [Real Environment Checklist](../guides/real-environment-checklist.md)
   and only then change the "unverified" wording — because the wording is the
   claim.

The part that should **not** change: the IR boundary, the deterministic SQL
generator, the static validator, and the approval gates. Those are the
architecture; the engine is a deployment detail.

## 18. Where exactly is the human in the loop?

At four distinct recorded gates, not one final checkbox:

```text
SQL approval → promotion review → release review → deployment review
```

Plus the refinement gate: the AI can propose a bounded change, but a human
decides. Approval is **content-addressed** — the human approves a hash of the
exact artifact — so the thing approved and the thing that runs cannot diverge.

## 19. How is the system auditable?

- **Content-addressed approvals.** The approved artifact is identified by hash;
  any change to the SQL (or to the joins it contains) changes the hash.
- **Immutable metric versions** in the registry, with a **rollback** path. You
  never edit a version; you create a new one.
- **An audit-event stream** per metric: who did what, at which gate, against
  which artifact.
- **Canonical hashing** of IR so the same structure hashes the same way
  regardless of dict ordering.
- **Artefacts at every stage.** Each stage writes an artifact that becomes the
  evidence for the next, so the chain from requirement to deployed metric is
  reconstructable.

The design rule underneath: an audit trail is only real if the thing you approve
is the thing that runs. Content-addressing is what enforces that.

## 20. Why does a Metric Registry exist at all?

Because a metric is not a one-off query — it is a **governed, versioned asset
with a lifecycle**.

Without a registry, you have SQL files in a folder and no answer to: which
version is active, who approved it, what evidence supported it, what changed
between versions, and how to roll back when the new one is worse.

With it: immutable versions, an active-version pointer, per-metric audit events,
rollback, and a release/promotion review that has the experiment evidence
attached. That is also what makes the platform's governance claims checkable
rather than aspirational.

## 21. What was the single hardest technical problem?

**Keeping the approval hash meaningful while extending the IR.**

Approval is content-addressed, which is what makes it trustworthy — but that
means the hash must cover *everything that affects the produced SQL*. When joins
were added for the second scenario, the join was a new part of the IR. If the
canonical hash had not been extended to include joins, you could have changed the
join, kept the same hash, and gotten a "valid" approval for a query nobody
reviewed.

It is a classic problem shape: the safety property (hash covers the artifact) and
the extensibility requirement (add a field) interact, and the interaction is
where the bug lives. The fix was extending canonical hashing *and* the tests that
pin it.

Honourable mention: the same class of bug at the persistence layer — state stored
both as a column *and* inside a JSON document must be written together, and the
read path must guard which one wins. That one bit me twice before it became a
written rule in `CONTRIBUTING.md`.

## 22. If you did it again, what would you change?

Being honest, in priority order:

1. **Write the second scenario earlier.** The multi-scenario proof is worth more
   than several of the single-scenario features I built before it. Building it
   late meant some generalisation work had to be retrofitted rather than designed
   in.
2. **Persist state in one place.** The column-plus-JSON duplication was a mistake
   I had to defend against repeatedly. One source of truth from day one.
3. **Force the demo path to exist from the start.** A "one command and it runs"
   path changes design decisions — it surfaces configuration coupling early.
4. **Treat the fixture identifier choice as a real decision.** One synthetic table
   name derived from a realistic-looking internal name, which then became
   expensive to change because artifacts embedded it. Naming is architecture when
   it gets hashed.
5. **Keep the phased reports.** That one I would do again exactly as-is — writing
   down what was *not* verified at each stage is why the final README's
   limitations section is accurate instead of retrofitted.
